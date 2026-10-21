"""Scientific and input-boundary checks for the synthetic WorldFork engine."""

from copy import deepcopy
import json
import math
import re
import unittest

import numpy as np

from worldfork.engine import (
    DEFAULTS,
    HORIZON,
    ROUTE_CAPACITY,
    ROUTE_COST,
    _make_worlds,
    _named_policies,
    _seed_streams,
    _simulate,
    bootstrap,
    run_experiment,
    validate_scenario,
)
from worldfork.forecast import trained_model


class ValidationTests(unittest.TestCase):
    def test_defaults_are_independent_and_normalized(self):
        value = validate_scenario({"name": "  Sample experiment  "})
        self.assertEqual(value["name"], "Sample experiment")
        value["seed"] = 999
        self.assertEqual(validate_scenario({}), DEFAULTS)

    def test_inclusive_numeric_boundaries(self):
        for values in [
            {"demand_multiplier": 0.6, "disruption": 0, "reserve_units": 0, "seed": 1, "worlds": 32},
            {"demand_multiplier": 2.5, "disruption": 0.85, "reserve_units": 200, "seed": 2147483647, "worlds": 512},
        ]:
            self.assertEqual(validate_scenario(values)["worlds"], values["worlds"])

    def test_nonfinite_boolean_string_and_huge_numeric_inputs_rejected(self):
        for field in ("demand_multiplier", "disruption", "reserve_units"):
            for value in (float("nan"), float("inf"), -float("inf"), True, False, np.bool_(True), "1", None, [], {}, 10**1000):
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        validate_scenario({field: value})

    def test_integers_are_not_coerced(self):
        for field in ("seed", "worlds"):
            for value in (True, False, 32.0, "32", float("nan"), float("inf"), None, 10**1000):
                with self.subTest(field=field, value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        validate_scenario({field: value})

    def test_out_of_range_fields_rejected(self):
        for field, values in {
            "demand_multiplier": (0.5999, 2.5001),
            "disruption": (-0.001, 0.851),
            "reserve_units": (-1, 201),
            "worlds": (31, 513),
            "seed": (0, 2147483648),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        validate_scenario({field: value})

    def test_unknown_fields_names_and_objectives_rejected(self):
        for value in ([], None, True, "{}", {"shell": "ignored"}, {"name": " "}, {"name": "x" * 81}, {"name": "control\ninside"}, {"name": "hidden\u200b"}, {"name": chr(0xd800)}, {"objective": "maximum-profit"}, {"objective": []}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    validate_scenario(value)


class ForecastTests(unittest.TestCase):
    def test_model_has_chronological_holdout_and_measured_naive_comparison(self):
        model = trained_model()
        self.assertEqual(model.metrics["train_samples"], (90 * 24 - 24) * 6)
        self.assertEqual(model.metrics["test_samples"], 30 * 24 * 6)
        self.assertGreater(model.metrics["mae"], 0)
        self.assertGreater(model.metrics["baseline_mae"], 0)
        # This is a regression check on the fixed synthetic benchmark, not a
        # promise that a model beats this baseline on arbitrary future data.
        self.assertLess(model.metrics["mae"], model.metrics["baseline_mae"])
        self.assertTrue(math.isfinite(model.metrics["r2"]))
        self.assertTrue(np.isfinite(model.coefficients).all())
        self.assertFalse(model.coefficients.flags.writeable)

    def test_bootstrap_does_not_expose_mutable_cached_metrics(self):
        data = bootstrap()
        data["model"]["features"].clear()
        data["network"]["nodes"].clear()
        again = bootstrap()
        self.assertGreater(len(again["model"]["features"]), 0)
        self.assertEqual(len(again["network"]["nodes"]), 9)
        self.assertEqual(len(again["network"]["edges"]), 10)


class WorldAndDispatchTests(unittest.TestCase):
    def test_search_and_evaluation_streams_are_distinct_and_reproducible(self):
        streams = _seed_streams(42)
        self.assertEqual(streams, _seed_streams(42))
        self.assertEqual(len(set(streams)), 3)
        scenario = validate_scenario({})
        training = _make_worlds(scenario, 32, streams[0])
        evaluation = _make_worlds(scenario, 32, streams[1])
        repeat = _make_worlds(scenario, 32, streams[1])
        np.testing.assert_array_equal(evaluation["demand"], repeat["demand"])
        self.assertFalse(np.array_equal(training["demand"], evaluation["demand"]))
        self.assertFalse(np.array_equal(training["capacity"], evaluation["capacity"]))

    def test_perturbations_reuse_same_random_futures(self):
        scenario = validate_scenario({})
        world = _make_worlds(scenario, 32, 123)
        changed = _make_worlds(dict(scenario, demand_multiplier=scenario["demand_multiplier"] * 1.1), 32, 123)
        np.testing.assert_allclose(changed["demand"], world["demand"] * 1.1)
        np.testing.assert_allclose(changed["forecast"], world["forecast"] * 1.1)
        np.testing.assert_array_equal(changed["capacity"], world["capacity"])
        reserve = _make_worlds(dict(scenario, reserve_units=scenario["reserve_units"] + 40), 32, 123)
        np.testing.assert_array_equal(reserve["demand"], world["demand"])
        np.testing.assert_allclose(reserve["capacity"] - world["capacity"], np.broadcast_to([13.6, 13.2, 13.2], world["capacity"].shape))

    def test_capacity_conservation_and_cost_under_extreme_scenarios(self):
        for changes in [
            {"demand_multiplier": 2.5, "disruption": 0.85, "reserve_units": 0},
            {"demand_multiplier": 0.6, "disruption": 0, "reserve_units": 200},
        ]:
            with self.subTest(scenario=changes):
                scenario = validate_scenario(changes)
                worlds = _make_worlds(scenario, 32, 918)
                result = _simulate(worlds, _named_policies(), scenario["reserve_units"])
                self.assertTrue(all(value < 1e-7 for value in result["checks"].values()))
                self.assertTrue((result["delivered"] <= result["demand"][None] + 1e-9).all())
                self.assertTrue((result["allocations"] >= 0).all())
                self.assertTrue((result["allocations"].sum(axis=-1) <= worlds["capacity"].sum(axis=1)[None] + 1e-8).all())
                self.assertTrue((result["allocations"] <= HORIZON * ROUTE_CAPACITY + 1e-8).all())
                self.assertTrue((result["allocations"][:, :, ROUTE_CAPACITY == 0] == 0).all())
                np.testing.assert_allclose(result["allocations"].sum(axis=(2, 3)), result["delivered"].sum(axis=-1) + result["waste"], atol=1e-8)
                actual_cost = (result["allocations"] * ROUTE_COST).sum(axis=(2, 3)) + 12 * scenario["reserve_units"] * 2.4
                np.testing.assert_allclose(result["cost"], actual_cost)

    def test_dispatch_cannot_peek_at_current_demand(self):
        forecasts = np.zeros((1, HORIZON, 6))
        forecasts[:, 0] = 60
        worlds = {"forecast": forecasts, "capacity": np.full((1, HORIZON, 3), 100.0), "demand": np.ones((1, HORIZON, 6))}
        low = _simulate(worlds, [_named_policies()[0]], 0)
        high_worlds = dict(worlds, demand=np.full((1, HORIZON, 6), 100.0))
        high = _simulate(high_worlds, [_named_policies()[0]], 0)
        # All later forecasts are zero, so total route flow is the hour-zero
        # decision. Only the after-dispatch delivery/waste split may differ.
        np.testing.assert_array_equal(low["allocations"], high["allocations"])
        self.assertLess(low["delivered"].sum(), high["delivered"].sum())
        self.assertGreater(low["waste"].sum(), high["waste"].sum())

    def test_zero_capacity_cannot_create_resources(self):
        worlds = {"forecast": np.full((2, HORIZON, 6), 70.0), "capacity": np.zeros((2, HORIZON, 3)), "demand": np.full((2, HORIZON, 6), 50.0)}
        result = _simulate(worlds, _named_policies(), 0)
        self.assertEqual(float(result["delivered"].sum()), 0)
        self.assertEqual(float(result["allocations"].sum()), 0)
        self.assertEqual(float(result["cost"].sum()), 0)


class ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = run_experiment({"worlds": 32})

    def test_output_is_finite_json_and_has_comparable_policies(self):
        json.dumps(self.result, allow_nan=False)
        self.assertTrue(re.fullmatch(r"[0-9a-f]{32}", self.result["id"]))
        self.assertEqual(self.result["recommended_policy"], "optimized")
        self.assertEqual(len(self.result["policies"]), 5)
        self.assertEqual(self.result["search"]["candidates"], 112)
        self.assertNotEqual(self.result["search"]["training_seed"], self.result["search"]["evaluation_seed"])
        self.assertTrue(all(item["passed"] for item in self.result["checks"]))
        self.assertGreater(len(self.result["limitations"]), 3)

    def test_reported_metrics_reconcile_with_resource_totals(self):
        demand_totals = []
        for policy in self.result["policies"]:
            for key in ("service_rate", "p10_service", "equity_gap", "score"):
                self.assertGreaterEqual(policy[key], 0)
                self.assertLessEqual(policy[key], 100)
            self.assertEqual(sum(bin_["count"] for bin_ in policy["distribution"]), 32)
            self.assertEqual(len(policy["timeline"]), 12)
            self.assertEqual(len(policy["allocations"]), 10)
            demand = sum(district["demand"] for district in policy["district_service"])
            delivered = sum(district["delivered"] for district in policy["district_service"])
            demand_totals.append(demand)
            self.assertAlmostEqual(policy["service_rate"], 100 * delivered / demand, delta=0.002)
            self.assertAlmostEqual(sum(route["units"] for route in policy["allocations"]), delivered + policy["waste_units"], delta=0.015)
            district_rates = [district["service_rate"] for district in policy["district_service"]]
            self.assertAlmostEqual(policy["equity_gap"], max(district_rates) - min(district_rates), delta=0.002)
            for hour in policy["timeline"]:
                self.assertLessEqual(hour["low"], hour["high"])
                self.assertLessEqual(hour["delivered"], hour["demand"])
                self.assertAlmostEqual(hour["service_rate"], 100 * hour["delivered"] / hour["demand"], delta=0.002)
        self.assertEqual(len(set(demand_totals)), 1)

    def test_fixed_seed_reproduces_all_scientific_output(self):
        first = deepcopy(self.result)
        second = run_experiment({"worlds": 32})
        for result in (first, second):
            result.pop("id")
            result.pop("created_at")
            result["search"].pop("elapsed_ms")
        self.assertEqual(first, second)

    def test_evaluation_sample_count_does_not_select_policy(self):
        # Altering held-out sample count changes estimated metrics, but cannot
        # change the search-selected policy or its training evidence.
        different_evaluation = run_experiment({"worlds": 64})
        chosen = next(policy for policy in self.result["policies"] if policy["id"] == "optimized")
        other = next(policy for policy in different_evaluation["policies"] if policy["id"] == "optimized")
        self.assertEqual(chosen["parameters"], other["parameters"])
        first_search = next(item for item in self.result["evidence"] if item["id"] == "search")
        other_search = next(item for item in different_evaluation["evidence"] if item["id"] == "search")
        self.assertEqual(first_search, other_search)

    def test_sensitivity_deltas_reference_frozen_policy_evaluation(self):
        chosen = next(policy for policy in self.result["policies"] if policy["id"] == "optimized")
        for item in self.result["sensitivity"]:
            self.assertAlmostEqual(item["service_rate"] - chosen["service_rate"], item["delta"], delta=0.002)
        evaluation = next(item for item in self.result["evidence"] if item["id"] == "evaluation")
        self.assertLessEqual(evaluation["metrics"]["paired_95_interval_low_pp"], evaluation["metrics"]["paired_95_interval_high_pp"])

    def test_focused_objectives_expose_a_real_benchmark_tradeoff(self):
        efficient = run_experiment({"objective": "efficiency", "worlds": 32})["policies"][-1]
        equitable = run_experiment({"objective": "equity", "worlds": 32})["policies"][-1]
        self.assertNotEqual(efficient["parameters"], equitable["parameters"])
        self.assertLess(efficient["cost"], equitable["cost"])
        self.assertGreater(efficient["equity_gap"], equitable["equity_gap"])


if __name__ == "__main__":
    unittest.main()
