"""Small trained forecasting model; all observations here are synthetic.

The chronological test partition is never used for fitting or policy search.
Simulation predictions use only known covariates and the previous observation.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np


BASE_DEMAND = np.array([52.0, 58.0, 47.0, 54.0, 45.0, 57.0])
DISTRICT_OFFSETS = np.array([0.0, 0.08, -0.04, 0.04, -0.02, 0.01])
EVENT_RESPONSE = np.array([1.2, 0.8, 1.05, 1.1, 0.7, 0.95])


def demand_signal(hour, weather, event, weekend):
    """Ground-truth synthetic mean, deliberately separate from fitted coefficients."""
    hour = np.asarray(hour)
    seasonal = (
        1.0
        + 0.18 * np.sin(2 * np.pi * (hour - 7) / 24)
        + 0.10 * np.cos(4 * np.pi * (hour - 8) / 24)
    )
    return (
        seasonal[..., None]
        + 0.12 * np.asarray(weather)[..., None]
        + 0.20 * np.asarray(event)[..., None] * EVENT_RESPONSE
        + 0.07 * np.asarray(weekend)[..., None]
        + DISTRICT_OFFSETS
    )


def _features(hour, weather, event, weekend, lag_ratio):
    """Rows are time/world-major then district; lag_ratio ends in six districts."""
    lag_ratio = np.asarray(lag_ratio, dtype=float)
    leading_shape = lag_ratio.shape[:-1]
    hour = np.broadcast_to(np.asarray(hour), leading_shape)
    weather = np.broadcast_to(np.asarray(weather), leading_shape)
    event = np.broadcast_to(np.asarray(event), leading_shape)
    weekend = np.broadcast_to(np.asarray(weekend), leading_shape)
    common = np.stack(
        [
            np.sin(2 * np.pi * hour / 24),
            np.cos(2 * np.pi * hour / 24),
            np.sin(4 * np.pi * hour / 24),
            np.cos(4 * np.pi * hour / 24),
            weather,
            event,
            weekend,
        ],
        axis=-1,
    )
    common = np.broadcast_to(common[..., None, :], (*leading_shape, 6, 7))
    districts = np.broadcast_to(np.eye(6), (*leading_shape, 6, 6))
    interaction = districts * event[..., None, None]
    features = np.concatenate(
        [common, lag_ratio[..., None], districts, interaction], axis=-1
    )
    return features.reshape(-1, 20)


@dataclass(frozen=True)
class ForecastModel:
    center: np.ndarray
    scale: np.ndarray
    coefficients: np.ndarray
    intercept: float
    metrics: dict

    def predict(self, hour, weather, event, weekend, lag_ratio):
        rows = _features(hour, weather, event, weekend, lag_ratio)
        ratio = ((rows - self.center) / self.scale) @ self.coefficients
        ratio += self.intercept
        ratio = np.maximum(ratio.reshape(np.asarray(lag_ratio).shape), 0.15)
        return ratio * BASE_DEMAND


@lru_cache(maxsize=1)
def trained_model():
    """Fit ridge once on the first 90 days; score on the following 30 days."""
    rng = np.random.default_rng(20261019)
    count = 120 * 24
    time = np.arange(count)
    day = time // 24
    hour = time % 24
    weather_daily = np.zeros(120)
    for index in range(1, 120):
        weather_daily[index] = 0.82 * weather_daily[index - 1] + rng.normal(0, 0.38)
    weather = np.clip(weather_daily[day] + rng.normal(0, 0.07, count), -1.8, 1.8)
    announced = (rng.random(120) < 0.24) * rng.uniform(0.6, 1.2, 120)
    event = announced[day] * ((hour >= 12) & (hour <= 19))
    weekend = (day % 7 >= 5).astype(float)
    signal = demand_signal(hour, weather, event, weekend)
    residual = np.zeros((count, 6))
    for index in range(1, count):
        residual[index] = (
            0.45 * residual[index - 1]
            + rng.normal(0, 0.045)
            + rng.normal(0, 0.065, 6)
        )
    ratios = np.maximum(signal + residual, 0.15)
    actual = ratios * BASE_DEMAND
    features = _features(hour[24:], weather[24:], event[24:], weekend[24:], ratios[23:-1])
    target = ratios[24:].reshape(-1)
    split = (90 * 24 - 24) * 6
    train_x = features[:split]
    train_y = target[:split]
    center = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    design = (train_x - center) / scale
    intercept = float(train_y.mean())
    coefficients = np.linalg.solve(
        design.T @ design + 4.0 * np.eye(design.shape[1]),
        design.T @ (train_y - intercept),
    )
    prediction = ((features[split:] - center) / scale) @ coefficients + intercept
    prediction = np.maximum(prediction.reshape(-1, 6), 0.15) * BASE_DEMAND
    truth = actual[90 * 24:]
    naive = actual[89 * 24:-24]
    mae = float(np.abs(prediction - truth).mean())
    baseline_mae = float(np.abs(naive - truth).mean())
    r2 = float(1 - np.square(prediction - truth).sum() / np.square(truth - truth.mean()).sum())
    metrics = {
        "name": "Chronological ridge demand forecaster",
        "train_samples": int(split),
        "test_samples": int(truth.size),
        "mae": round(mae, 4),
        "baseline_mae": round(baseline_mae, 4),
        "r2": round(r2, 4),
        "features": [
            "hour: first and second sine/cosine harmonics",
            "known weather index",
            "announced event intensity",
            "weekend indicator",
            "previous observed hourly demand / district base demand",
            "district indicators",
            "event intensity × district indicators",
        ],
        "data_source": "120 days of generated hourly observations for six fictional districts; no real operational data.",
        "split": "First 90 days fit; final 30 days test. The first 24 hours supply lag features and the previous-day naive baseline. Standardization uses training rows only.",
        "mae_units": "resource units per district-hour",
        "baseline": "Previous day's observed demand at the same hour",
        "regularization": 4.0,
    }
    for array in (center, scale, coefficients):
        array.setflags(write=False)
    return ForecastModel(center, scale, coefficients, intercept, metrics)
