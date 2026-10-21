"""Reproducible counterfactual experiments on a fictional resource network.

The stages are numerical algorithms, not language-model agents. A learned
forecast drives prepositioning before each hour's demand is observed. Policies
are selected on search worlds, frozen, and compared on separate evaluation
worlds. Nothing in this module calls a model provider or executes user code.
"""

from copy import deepcopy
from datetime import datetime, timezone
from itertools import product
import math
from numbers import Integral, Real
from time import perf_counter
import unicodedata
from uuid import uuid4

import numpy as np

from .forecast import BASE_DEMAND, demand_signal, trained_model


VERSION = "0.1.0"
HORIZON = 12
TRAINING_WORLDS = 64
HOURS = np.arange(8, 20)
HUB_CAPACITY = np.array([112.0, 132.0, 114.0])
ROUTE_CAPACITY = np.array([
    [78.0, 72.0, 48.0, 0.0, 0.0, 0.0],
    [0.0, 55.0, 70.0, 72.0, 53.0, 0.0],
    [0.0, 0.0, 0.0, 50.0, 72.0, 78.0],
])
ROUTE_COST = np.array([
    [1.25, 1.60, 2.45, 0.0, 0.0, 0.0],
    [0.0, 1.85, 1.35, 1.45, 2.25, 0.0],
    [0.0, 0.0, 0.0, 2.40, 1.45, 1.30],
])
HUBS = [
    {"id": "west", "name": "West depot", "type": "hub", "x": 160, "y": 110, "capacity": 112},
    {"id": "central", "name": "Central depot", "type": "hub", "x": 500, "y": 85, "capacity": 132},
    {"id": "east", "name": "East depot", "type": "hub", "x": 840, "y": 110, "capacity": 114},
]
DISTRICTS = [
    {"id": "riverside", "name": "Riverside", "type": "district", "x": 105, "y": 400, "base_demand": 52},
    {"id": "midtown", "name": "Midtown", "type": "district", "x": 275, "y": 300, "base_demand": 58},
    {"id": "oldtown", "name": "Old Town", "type": "district", "x": 430, "y": 460, "base_demand": 47},
    {"id": "southbank", "name": "Southbank", "type": "district", "x": 585, "y": 345, "base_demand": 54},
    {"id": "hillview", "name": "Hillview", "type": "district", "x": 745, "y": 455, "base_demand": 45},
    {"id": "harbor", "name": "Harbor", "type": "district", "x": 900, "y": 330, "base_demand": 57},
]
DEFAULTS = {
    "name": "Western hub outage",
    "demand_multiplier": 1.15,
    "disruption": 0.45,
    "reserve_units": 20.0,
    "objective": "balanced",
    "seed": 42,
    "worlds": 128,
}
OBJECTIVE_WEIGHTS = {
    "balanced": (0.42, 0.18, 0.24, 0.16),
    "service": (0.80, 0.20, 0.0, 0.0),
    "efficiency": (0.0, 0.0, 0.0, 1.0),
    "equity": (0.0, 0.0, 1.0, 0.0),
}
PARAMETER_NAMES = ("hedge", "distance_weight", "equity_weight", "scarcity_weight", "demand_exponent")


def validate_scenario(data):
    """Normalize the narrow public input schema without coercing booleans/strings."""
    if not isinstance(data, dict):
        raise ValueError("Scenario must be a JSON object.")
    if set(data) - set(DEFAULTS):
        raise ValueError("Scenario contains unknown fields.")
    result = dict(DEFAULTS)
    result.update(data)
    name = result["name"]
    if not isinstance(name, str):
        raise ValueError("name must be a string of 1 to 80 characters.")
    name = name.strip()
    if not 1 <= len(name) <= 80 or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in name):
        raise ValueError("name must contain 1 to 80 visible characters.")
    result["name"] = name
    for field, low, high in [
        ("demand_multiplier", 0.6, 2.5),
        ("disruption", 0.0, 0.85),
        ("reserve_units", 0.0, 200.0),
    ]:
        value = result[field]
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
            raise ValueError(f"{field} must be a finite number between {low} and {high}.")
        if not low <= value <= high or not math.isfinite(value):
            raise ValueError(f"{field} must be a finite number between {low} and {high}.")
        result[field] = float(value)
    for field, low, high in [("seed", 1, 2147483647), ("worlds", 32, 512)]:
        value = result[field]
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, Integral) or not low <= value <= high:
            raise ValueError(f"{field} must be an integer between {low} and {high}.")
        result[field] = int(value)
    if not isinstance(result["objective"], str) or result["objective"] not in OBJECTIVE_WEIGHTS:
        raise ValueError("objective must be balanced, service, efficiency, or equity.")
    return result


def _network():
    edges = [
        {"source": hub["id"], "target": district["id"], "cost": float(ROUTE_COST[h, d]), "capacity": float(ROUTE_CAPACITY[h, d])}
        for h, hub in enumerate(HUBS)
        for d, district in enumerate(DISTRICTS)
        if ROUTE_CAPACITY[h, d] > 0
    ]
    return {"nodes": deepcopy(HUBS + DISTRICTS), "edges": edges}


def bootstrap():
    presets = [
        ("outage", "Western hub outage", "A sustained western capacity loss with a small reserve.", {}),
        ("routine", "Ordinary operations", "Modest uncertainty without a planned depot disruption.", {"name": "Ordinary operations", "demand_multiplier": 1.0, "disruption": 0.0, "reserve_units": 0.0}),
        ("surge", "Citywide demand surge", "High demand tests scarcity and geographic service tradeoffs.", {"name": "Citywide demand surge", "demand_multiplier": 1.6, "disruption": 0.1, "reserve_units": 40.0, "objective": "service"}),
        ("fairness", "Protect the isolated districts", "A severe western outage makes equal coverage difficult.", {"name": "Protect the isolated districts", "demand_multiplier": 1.25, "disruption": 0.7, "reserve_units": 50.0, "objective": "equity"}),
    ]
    return {
        "defaults": validate_scenario({}),
        "presets": [{"id": id_, "name": name, "description": description, "scenario": validate_scenario(overrides)} for id_, name, description, overrides in presets],
        "network": _network(),
        "model": deepcopy(trained_model().metrics),
        "version": VERSION,
    }


def _seed_streams(seed):
    children = np.random.SeedSequence(seed).spawn(3)
    return tuple(int(child.generate_state(1, dtype=np.uint32)[0]) for child in children)


def _make_worlds(scenario, count, seed):
    """Known covariates + unseen innovations. RNG draw order is scenario-independent."""
    rng = np.random.default_rng(seed)
    weather = np.clip(rng.normal(0, 0.65, (count, 1)) + rng.normal(0, 0.08, (count, HORIZON)), -1.8, 1.8)
    intensity = (rng.random(count) < 0.30) * rng.uniform(0.6, 1.2, count)
    event = intensity[:, None] * ((HOURS[None, :] >= 12) & (HOURS[None, :] <= 19))
    weekend = (rng.random(count) < 2 / 7).astype(float)
    residual = rng.normal(0, 0.075, (count, 6))
    lag = np.maximum(demand_signal(np.full(count, 7), weather[:, 0], np.zeros(count), weekend) + residual, 0.15)
    shock_start = rng.integers(2, 9, count)
    shock_size = (rng.random(count) < 0.20) * rng.uniform(0.15, 0.40, count)
    shock_districts = rng.uniform(0.4, 1.0, (count, 6))
    demand = np.empty((count, HORIZON, 6))
    forecast = np.empty_like(demand)
    model = trained_model()
    for step, hour in enumerate(HOURS):
        # Dispatch receives this prediction, never the current realized demand.
        forecast[:, step] = model.predict(np.full(count, hour), weather[:, step], event[:, step], weekend, lag) * scenario["demand_multiplier"]
        residual = 0.45 * residual + rng.normal(0, 0.045, (count, 1)) + rng.normal(0, 0.065, (count, 6))
        surprise = shock_size * ((step >= shock_start) & (step < shock_start + 3))
        ratio = np.maximum(demand_signal(np.full(count, hour), weather[:, step], event[:, step], weekend) + residual + surprise[:, None] * shock_districts, 0.15)
        demand[:, step] = ratio * BASE_DEMAND * scenario["demand_multiplier"]
        lag = ratio
    capacity = HUB_CAPACITY * rng.uniform(0.90, 1.0, (count, HORIZON, 3))
    capacity[:, :, 0] *= 1.0 - scenario["disruption"]
    outage = rng.random(count) < 0.18
    outage_hub = rng.integers(0, 3, count)
    outage_start = rng.integers(1, 10, count)
    for step in range(HORIZON):
        hit = outage & (step >= outage_start) & (step < outage_start + 2)
        capacity[np.arange(count)[hit], step, outage_hub[hit]] *= 0.55
    capacity += scenario["reserve_units"] * np.array([0.34, 0.33, 0.33])
    return {"demand": demand, "forecast": forecast, "capacity": capacity, "seed": int(seed)}


def _policy(id_, name, description, values):
    return {"id": id_, "name": name, "description": description, "parameters": dict(zip(PARAMETER_NAMES, values))}


def _named_policies():
    return [
        _policy("baseline", "Forecast proportional", "Spreads forecast-led allocations with modest routing and fairness adjustments.", (0.04, 0.65, 0.8, 0.45, 1.0)),
        _policy("efficient", "Nearest route first", "Strongly favors inexpensive routes and avoids a forecast hedge.", (0.0, 4.0, 0.0, 0.0, 1.0)),
        _policy("equitable", "Coverage first", "Prioritizes districts with fewer connections and earlier service deficits.", (0.10, 0.2, 6.0, 1.5, 0.5)),
        _policy("service", "Demand hedge", "Plans above forecast demand to limit shortages, with more waste possible.", (0.20, 0.35, 0.5, 0.3, 1.3)),
    ]


def _candidates():
    result = _named_policies()
    for index, values in enumerate(product((0.0, 0.08, 0.18), (0.15, 1.0, 3.0), (0.0, 3.0), (0.0, 0.8, 1.6), (0.6, 1.2))):
        result.append(_policy(f"candidate-{index}", "Bounded search candidate", "", values))
    return result


def _simulate(worlds, policies, reserve_units):
    """Batched constrained proportional dispatch with ten redistribution rounds.

    Capacity is known at dispatch time. Target quantities come from forecasts;
    retrospective fairness uses only demand and service from previous hours.
    Current demand is revealed after routing. Excess dispatched supply is waste.
    """
    demand, forecasts, capacities = worlds["demand"], worlds["forecast"], worlds["capacity"]
    policy_count, world_count = len(policies), demand.shape[0]
    params = {name: np.array([p["parameters"][name] for p in policies]) for name in PARAMETER_NAMES}
    mask = ROUTE_CAPACITY > 0
    degree = mask.sum(axis=0)
    scarcity = (2.0 / degree)[None, None, None, :] ** params["scarcity_weight"][:, None, None, None]
    cost = np.where(mask, ROUTE_COST, 1.0)
    preference = mask[None, None] * scarcity / cost[None, None] ** params["distance_weight"][:, None, None, None]
    delivered_cumulative = np.zeros((policy_count, world_count, 6))
    demand_cumulative = np.zeros((world_count, 6))
    allocations = np.zeros((policy_count, world_count, 3, 6))
    hourly_delivered = np.zeros((policy_count, world_count, HORIZON))
    wasted = np.zeros((policy_count, world_count))
    transport_cost = np.zeros((policy_count, world_count))
    checks = {"hub_capacity": 0.0, "route_capacity": 0.0, "demand_bound": 0.0, "negative_flow": 0.0, "conservation": 0.0, "capacity_balance": 0.0}
    for step in range(HORIZON):
        target = forecasts[None, :, step, :] * (1 + params["hedge"][:, None, None])
        history_service = np.divide(delivered_cumulative, demand_cumulative[None], out=np.ones_like(delivered_cumulative), where=demand_cumulative[None] > 0)
        deficit = np.maximum(history_service.mean(axis=-1, keepdims=True) - history_service, 0)
        fair_priority = 1 + params["equity_weight"][:, None, None] * deficit
        route_preference = preference * fair_priority[:, :, None, :]
        available = np.broadcast_to(capacities[None, :, step, :], (policy_count, world_count, 3)).copy()
        remaining = target.copy()
        flow = np.zeros((policy_count, world_count, 3, 6))
        for _ in range(10):
            route_remaining = np.maximum(ROUTE_CAPACITY[None, None] - flow, 0)
            priority = route_preference * np.maximum(remaining[:, :, None, :], 0) ** params["demand_exponent"][:, None, None, None]
            priority *= route_remaining > 1e-9
            norm = priority.sum(axis=-1, keepdims=True)
            shares = np.divide(priority, norm, out=np.zeros_like(priority), where=norm > 0)
            proposal = np.minimum(available[..., None] * shares, route_remaining)
            incoming = proposal.sum(axis=2)
            scale = np.minimum(1, np.divide(remaining, incoming, out=np.ones_like(incoming), where=incoming > 0))
            accepted = proposal * scale[:, :, None, :]
            flow += accepted
            available = np.maximum(available - accepted.sum(axis=-1), 0)
            remaining = np.maximum(remaining - accepted.sum(axis=2), 0)
        positioned = flow.sum(axis=2)
        delivered = np.minimum(positioned, demand[None, :, step, :])
        waste = positioned - delivered
        delivered_cumulative += delivered
        demand_cumulative += demand[:, step]
        allocations += flow
        hourly_delivered[:, :, step] = delivered.sum(axis=-1)
        wasted += waste.sum(axis=-1)
        transport_cost += (flow * ROUTE_COST).sum(axis=(2, 3))
        checks["hub_capacity"] = max(checks["hub_capacity"], float(np.maximum(flow.sum(axis=-1) - capacities[None, :, step], 0).max()))
        checks["route_capacity"] = max(checks["route_capacity"], float(np.maximum(flow - ROUTE_CAPACITY, 0).max()))
        checks["demand_bound"] = max(checks["demand_bound"], float(np.maximum(delivered - demand[None, :, step], 0).max()))
        checks["negative_flow"] = max(checks["negative_flow"], float(max(0, -flow.min(), -delivered.min(), -waste.min())))
        checks["conservation"] = max(checks["conservation"], float(np.abs(flow.sum(axis=(2, 3)) - delivered.sum(axis=-1) - waste.sum(axis=-1)).max()))
        checks["capacity_balance"] = max(checks["capacity_balance"], float(np.abs(flow.sum(axis=-1) + available - capacities[None, :, step]).max()))
    return {
        "delivered": delivered_cumulative,
        "demand": demand.sum(axis=1),
        "hourly_delivered": hourly_delivered,
        "hourly_demand": demand.sum(axis=-1),
        "allocations": allocations,
        "waste": wasted,
        "cost": transport_cost + HORIZON * reserve_units * 2.4,
        "checks": checks,
    }


def _metrics(result, objective):
    delivered = result["delivered"].sum(axis=-1)
    demand = result["demand"].sum(axis=-1)
    world_service = delivered / demand[None]
    service = delivered.sum(axis=1) / demand.sum()
    p10 = np.quantile(world_service, 0.10, axis=1)
    district_rates = result["delivered"].sum(axis=1) / result["demand"].sum(axis=0)[None]
    gap = district_rates.max(axis=1) - district_rates.min(axis=1)
    cost = result["cost"].mean(axis=1)
    unit_cost = result["cost"].sum(axis=1) / np.maximum(delivered.sum(axis=1), 1e-12)
    efficiency = 1 - np.minimum(unit_cost / 4.0, 1.0)
    w_service, w_p10, w_equity, w_efficiency = OBJECTIVE_WEIGHTS[objective]
    score = 100 * (w_service * service + w_p10 * p10 + w_equity * (1 - gap) + w_efficiency * efficiency)
    return {"service": 100 * service, "p10": 100 * p10, "gap": 100 * gap, "cost": cost, "score": score, "district_rates": 100 * district_rates, "world_service": 100 * world_service, "unit_cost": unit_cost}


def _pack_policy(policy, index, result, metrics):
    delivered = result["delivered"][index].mean(axis=0)
    demand = result["demand"].mean(axis=0)
    hourly_rates = 100 * result["hourly_delivered"][index] / result["hourly_demand"]
    hourly_service = 100 * result["hourly_delivered"][index].sum(axis=0) / result["hourly_demand"].sum(axis=0)
    low, high = np.quantile(hourly_rates, [0.1, 0.9], axis=0)
    allocation = result["allocations"][index].mean(axis=0)
    histogram, bins = np.histogram(metrics["world_service"][index], bins=np.linspace(0, 100.000001, 21))
    return {
        **deepcopy(policy),
        "service_rate": round(float(metrics["service"][index]), 3),
        "p10_service": round(float(metrics["p10"][index]), 3),
        "cost": round(float(metrics["cost"][index]), 2),
        "equity_gap": round(float(metrics["gap"][index]), 3),
        "score": round(float(metrics["score"][index]), 3),
        "waste_units": round(float(result["waste"][index].mean()), 2),
        "unit_cost": round(float(metrics["unit_cost"][index]), 4),
        "district_service": [{"id": district["id"], "name": district["name"], "service_rate": round(float(metrics["district_rates"][index, d]), 3), "delivered": round(float(delivered[d]), 3), "demand": round(float(demand[d]), 3)} for d, district in enumerate(DISTRICTS)],
        "timeline": [{"hour": int(hour), "service_rate": round(float(hourly_service[t]), 3), "low": round(float(low[t]), 3), "high": round(float(high[t]), 3), "demand": round(float(result["hourly_demand"][:, t].mean()), 3), "delivered": round(float(result["hourly_delivered"][index, :, t].mean()), 3)} for t, hour in enumerate(HOURS)],
        "allocations": [{"source": hub["id"], "target": district["id"], "units": round(float(allocation[h, d]), 3)} for h, hub in enumerate(HUBS) for d, district in enumerate(DISTRICTS) if ROUTE_CAPACITY[h, d] > 0],
        "distribution": [{"bin": round(float((bins[i] + bins[i + 1]) / 2), 1), "count": int(count)} for i, count in enumerate(histogram)],
    }


def _paired_interval(result, chosen_index, baseline_index, seed):
    rng = np.random.default_rng(seed)
    world_count = result["demand"].shape[0]
    indices = rng.integers(0, world_count, (400, world_count))
    delivered_delta = result["delivered"][chosen_index].sum(axis=-1) - result["delivered"][baseline_index].sum(axis=-1)
    demand = result["demand"].sum(axis=-1)
    differences = 100 * delivered_delta[indices].sum(axis=1) / demand[indices].sum(axis=1)
    return tuple(float(value) for value in np.quantile(differences, [0.025, 0.975]))


def run_experiment(scenario):
    started = perf_counter()
    scenario = validate_scenario(scenario)
    model_metrics = deepcopy(trained_model().metrics)
    training_seed, evaluation_seed, interval_seed = _seed_streams(scenario["seed"])
    training = _make_worlds(scenario, TRAINING_WORLDS, training_seed)
    candidates = _candidates()
    training_result = _simulate(training, candidates, scenario["reserve_units"])
    training_metrics = _metrics(training_result, scenario["objective"])
    winner = int(np.argmax(training_metrics["score"]))
    optimized = deepcopy(candidates[winner])
    optimized.update({"id": "optimized", "name": "Searched policy", "description": "Selected by the scenario objective on separate search worlds; parameters are frozen before evaluation."})
    policies = _named_policies() + [optimized]
    evaluation = _make_worlds(scenario, scenario["worlds"], evaluation_seed)
    result = _simulate(evaluation, policies, scenario["reserve_units"])
    metrics = _metrics(result, scenario["objective"])
    chosen_index = len(policies) - 1
    packed = [_pack_policy(policy, i, result, metrics) for i, policy in enumerate(policies)]
    delta = float(metrics["service"][chosen_index] - metrics["service"][0])
    ci_low, ci_high = _paired_interval(result, chosen_index, 0, interval_seed)
    sensitivity = []
    all_checks = [training_result["checks"], result["checks"]]
    perturbations = [
        ("demand_multiplier", min(scenario["demand_multiplier"] * 1.1, 2.5) if scenario["demand_multiplier"] < 2.5 else 2.25, "Demand"),
        ("disruption", min(scenario["disruption"] + 0.10, 0.85) if scenario["disruption"] < 0.85 else 0.75, "West capacity loss"),
        ("reserve_units", min(scenario["reserve_units"] + 40, 200) if scenario["reserve_units"] < 200 else 160.0, "Reserve units / hour"),
    ]
    for field, value, label in perturbations:
        changed = dict(scenario, **{field: float(value)})
        changed_worlds = _make_worlds(changed, scenario["worlds"], evaluation_seed)
        changed_result = _simulate(changed_worlds, [optimized], changed["reserve_units"])
        all_checks.append(changed_result["checks"])
        changed_rate = float(_metrics(changed_result, scenario["objective"])["service"][0])
        change = f"{scenario[field]:.2f} → {value:.2f}"
        sensitivity.append({"label": label, "change": change, "service_rate": round(changed_rate, 3), "delta": round(changed_rate - float(metrics["service"][chosen_index]), 3)})
    violation = {key: max(item[key] for item in all_checks) for key in result["checks"]}
    check_labels = {
        "hub_capacity": "Dispatch stays within each hub's realized hourly capacity",
        "route_capacity": "Each hourly route respects its capacity",
        "demand_bound": "Delivered units never exceed actual demand",
        "negative_flow": "Dispatch, delivery, and waste remain nonnegative",
        "conservation": "Dispatched units equal delivered units plus waste",
        "capacity_balance": "Dispatched units plus unused capacity equal available supply",
    }
    checks = [{"name": label, "passed": bool(violation[key] <= 1e-7), "detail": f"Maximum violation {violation[key]:.2e} units across search, evaluation, and sensitivity simulations; tolerance 1e-7."} for key, label in check_labels.items()]
    checks.append({"name": "Search and evaluation use separate random streams", "passed": len({training_seed, evaluation_seed, interval_seed}) == 3, "detail": f"Search seed {training_seed}; evaluation seed {evaluation_seed}; interval seed {interval_seed}. Sensitivity intentionally reuses evaluation futures."})
    if not all(check["passed"] for check in checks):
        raise RuntimeError("Simulation invariant failed; the experiment was not accepted.")
    ambiguity = "The paired interval includes zero; this run does not resolve a service advantage." if ci_low <= 0 <= ci_high else "The paired interval describes Monte Carlo sampling uncertainty within this simulator only."
    summary = (
        f"The {scenario['objective']} search selected a policy serving {metrics['service'][chosen_index]:.1f}% of demand across {scenario['worlds']} held-out synthetic futures, "
        f"versus {metrics['service'][0]:.1f}% for the baseline ({delta:+.2f} percentage points). "
        f"{ambiguity} The search balances its configured objective; a service gain is not guaranteed."
    )
    weights = OBJECTIVE_WEIGHTS[scenario["objective"]]
    evidence = [
        {"id": "source", "role": "Data provenance", "title": "A fictional city, with explicit assumptions", "detail": "All history, demand, disruptions, routes, capacities, and costs are generated. Known weather and announced events are available before dispatch. Unannounced demand innovations are not.", "status": "info", "metrics": {"districts": 6, "hubs": 3, "routes": int((ROUTE_CAPACITY > 0).sum()), "horizon_hours": HORIZON, "historical_days": 120}},
        {"id": "forecast", "role": "Forecast model", "title": "Ridge regression scored on later dates", "detail": "Training standardization and coefficients use the first 90 days. The final 30 days are held out. MAE is in resource units per district-hour; the comparison predicts the previous day's demand at the same hour.", "status": "pass" if model_metrics["mae"] < model_metrics["baseline_mae"] else "warning", "metrics": {"train_samples": model_metrics["train_samples"], "test_samples": model_metrics["test_samples"], "mae": model_metrics["mae"], "naive_mae": model_metrics["baseline_mae"], "r2": model_metrics["r2"]}},
        {"id": "search", "role": "Policy search", "title": "Bounded parameter search; no optimality claim", "detail": "Four reference policies and 108 grid candidates share 64 search worlds. The highest training objective is frozen before evaluation. The search does not observe evaluation outcomes.", "status": "info", "metrics": {"candidates": len(candidates), "worlds": TRAINING_WORLDS, "training_seed": training_seed, "training_score": round(float(training_metrics["score"][winner]), 3), "objective_weights": dict(zip(("service", "p10_service", "one_minus_equity_gap", "cost_efficiency"), weights))}},
        {"id": "evaluation", "role": "Held-out evaluation", "title": "Competing policies see the same unseen futures", "detail": "Service is delivered / requested across all held-out district-hours. P10 is the 10th percentile of each world's horizon service rate. A 400-resample paired bootstrap compares the searched policy with baseline; it is not an operational confidence guarantee.", "status": "pass" if ci_low > 0 else "warning", "metrics": {"evaluation_worlds": scenario["worlds"], "evaluation_seed": evaluation_seed, "service_delta_pp": round(delta, 3), "paired_95_interval_low_pp": round(ci_low, 3), "paired_95_interval_high_pp": round(ci_high, 3)}},
        {"id": "constraints", "role": "Conservation audit", "title": "Every simulated dispatch checked", "detail": "Divisible resources are prepositioned before demand arrives. Same-hour deliveries are capped at actual demand; excess dispatch is waste, and unmet demand expires. Hub, route, conservation, and nonnegativity checks cover every policy and perturbation.", "status": "pass", "metrics": {"checks_passed": len(checks), "max_numeric_violation_units": float(max(violation.values())), "chosen_mean_waste_units": round(float(result["waste"][chosen_index].mean()), 2)}},
        {"id": "sensitivity", "role": "Sensitivity analysis", "title": "One assumption changes at a time", "detail": "The selected policy is rerun with altered demand, west capacity loss, and reserve capacity. Identical evaluation random draws isolate each change. Parameters are not searched again.", "status": "info", "metrics": {"perturbations": len(sensitivity), "shared_random_futures": True}},
        {"id": "objective", "role": "Metric definitions", "title": "Utility and credits are modeling choices", "detail": "Cost includes every dispatched unit times its route credit cost plus 12 × reserve units/hour × 2.4 credits. Equity gap is the highest minus lowest district service rate. Utility combines service, P10, 1−gap, and 1−min(credits per delivered unit/4,1), using the selected weights. Utility is not a success probability.", "status": "info", "metrics": {"cost_unit": "synthetic credits", "reserve_credit_rate_per_unit_hour": 2.4, "efficiency_reference_credits_per_unit": 4.0, "timeline": "per-hour service with 10th/90th percentile bands"}},
    ]
    return {
        "id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario,
        "model": model_metrics,
        "network": _network(),
        "recommended_policy": "optimized",
        "summary": summary,
        "policies": packed,
        "evidence": evidence,
        "checks": checks,
        "search": {"candidates": len(candidates), "training_worlds": TRAINING_WORLDS, "evaluation_worlds": scenario["worlds"], "seed": scenario["seed"], "training_seed": training_seed, "evaluation_seed": evaluation_seed, "elapsed_ms": round((perf_counter() - started) * 1000, 1), "method": "Exhaustive bounded grid plus four references; vectorized constrained proportional dispatch. Winner selected only on search worlds."},
        "sensitivity": sensitivity,
        "limitations": [
            "Synthetic demonstration only: no real operational data, calibrated demand distribution, live dispatch, or claim of real-world savings.",
            "The historical model and future worlds share a designed data-generating family. The chronological test measures performance inside that family, not generalization to a real city.",
            "Weather, announced events, and current hub capacity are treated as known; forecast error for those signals is not modeled.",
            "Resources are divisible, travel completes within an hour, route capacity is fixed, and unmet demand expires. There is no inventory carryover, integer vehicle routing, or travel-time congestion.",
            "Search covers a bounded heuristic family, not all feasible policies. A recommendation can underperform a reference on held-out worlds or on an individual metric.",
            "Monte Carlo bands and the paired interval express sampling variation under assumed distributions. They do not bound unmodeled events or establish causal effects in reality.",
            "Costs use arbitrary credits and reserves have an assumed fixed commitment charge. The equity objective measures geographic service spread, not demographic or individual fairness.",
        ],
    }
