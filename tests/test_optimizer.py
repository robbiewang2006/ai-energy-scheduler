from dataclasses import replace
from pathlib import Path
import csv
import tempfile
import unittest

import numpy as np

from energy_data import load_energy_inputs
from optimizer import finite_difference_gradient, optimize_schedule
from scheduler import (
    Battery, IT_CAPACITY_KW, JOBS, Job, PUE, SYNTHETIC_INPUTS,
    constraint_violation, dispatch, main, objective,
    summarize, verify_schedule,
)
from test_scheduler import GRID, _price_row, write_bundle


class GradientSchedulerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.inputs = write_bundle(
            self.path, prices=[
                _price_row(GRID, "2024-12", 0.9),
                _price_row(GRID, "2025-01", 0.1),
            ],
        )
        self.jobs = [Job("tiny", 0, 2, 10)]
        self.battery = Battery()

    def test_tiny_optimizer_matches_exhaustive_best(self):
        schedule, history = optimize_schedule(self.jobs, self.battery, self.inputs)
        feasible_scores = [
            objective({"tiny": [float(x), 10.0 - x]}, self.battery, 0.2, self.inputs)
            for x in range(11)
        ]
        self.assertAlmostEqual(
            objective(schedule, self.battery, 0.2, self.inputs),
            min(feasible_scores), places=6,
        )
        self.assertLess(history[-1]["score_rmb"], history[0]["score_rmb"])
        self.assertTrue(all(entry["feasible"] for entry in history))
        self.assertTrue(all(
            after["score_rmb"] <= before["score_rmb"]
            for before, after in zip(history, history[1:])
        ))
        self.assertAlmostEqual(schedule["tiny"][0], 0, places=6)
        self.assertAlmostEqual(schedule["tiny"][1], 10, places=6)

    def test_coupled_projection_completes_jobs_and_respects_windows(self):
        inputs = write_bundle(
            self.path, timestamps=[
                "2025-01-01T00:00:00", "2025-01-01T01:00:00",
                "2025-01-01T02:00:00",
            ],
        )
        jobs = [Job("early", 0, 2, 25), Job("late", 1, 3, 50),
                Job("empty", 0, 1, 0)]
        for initialization in ("uniform", "random"):
            schedule, history = optimize_schedule(
                jobs, self.battery, inputs, initialization=initialization,
                max_iterations=3,
            )
            verify_schedule(jobs, schedule, inputs)
            self.assertAlmostEqual(schedule["early"][0], 25, places=6)
            self.assertAlmostEqual(schedule["early"][1], 0, places=6)
            self.assertEqual(schedule["late"][0], 0)
            for hour in range(3):
                self.assertLessEqual(
                    inputs.protected_it_kw[hour]
                    + sum(values[hour] for values in schedule.values()),
                    IT_CAPACITY_KW + 1e-6,
                )
            self.assertLess(history[-1]["max_constraint_violation_kwh"], 1e-6)

    def test_dispatch_balance_and_battery_conservation(self):
        schedule, _ = optimize_schedule(
            JOBS, self.battery, max_iterations=5, initialization="uniform"
        )
        rows = dispatch(schedule, self.battery)
        soc = 0
        self.assertGreater(sum(row["battery_charge_kwh"] for row in rows), 0)
        self.assertGreater(sum(row["battery_discharge_kwh"] for row in rows), 0)
        for row in rows:
            charge, discharge = row["battery_charge_kwh"], row["battery_discharge_kwh"]
            supplied = sum(
                row[f"source_{source_id}_kwh"] for source_id in SYNTHETIC_INPUTS.sources
            )
            self.assertAlmostEqual(supplied + discharge, row["facility_kwh"] + charge)
            soc += charge * self.battery.charge_efficiency
            soc -= discharge / self.battery.discharge_efficiency
            self.assertAlmostEqual(soc, row["battery_soc_kwh"])
            self.assertGreaterEqual(soc, -1e-9)
            self.assertLessEqual(soc, self.battery.capacity_kwh + 1e-9)
            self.assertLessEqual(max(charge, discharge), self.battery.power_kw)
            self.assertAlmostEqual(
                row["facility_kwh"],
                PUE * (row["protected_it_kwh"] + row["flexible_it_kwh"]),
            )
        self.assertLess(
            constraint_violation(JOBS, schedule, rows, self.battery), 1e-6
        )

    def test_raw_score_and_normalization_use_fixed_facility_energy(self):
        totals = []
        for allocation in ([10.0, 0.0], [0.0, 10.0]):
            rows = dispatch({"tiny": allocation}, self.battery, self.inputs)
            metrics = summarize("test", rows, carbon_weight=0.3)
            totals.append(metrics["facility_energy_kwh"])
            self.assertEqual(metrics["facility_energy_kwh"], 36)
            self.assertAlmostEqual(
                metrics["score_rmb"], metrics["cost_rmb"] + 0.3 * metrics["emissions_kg"]
            )
            factor = 1e9 / 36
            self.assertAlmostEqual(metrics["normalization_factor"], factor)
            self.assertAlmostEqual(
                metrics["cost_per_1twh_rmb"], metrics["cost_rmb"] * factor, delta=1e-6
            )
            self.assertAlmostEqual(
                metrics["score_per_1twh_rmb"],
                metrics["cost_per_1twh_rmb"] + 0.3 * metrics["emissions_per_1twh_kg"],
                delta=1e-6,
            )
            custom = summarize("test", rows, annual_demand_reference_kwh=1000)
            self.assertAlmostEqual(custom["cost_per_reference_rmb"], metrics["cost_rmb"] * 1000 / 36)
            self.assertEqual(custom["cost_per_1twh_rmb"], metrics["cost_per_1twh_rmb"])
        self.assertEqual(totals[0], totals[1])

    def test_battery_charging_is_not_additional_facility_demand(self):
        schedule, _ = optimize_schedule(JOBS, self.battery, max_iterations=1)
        rows = dispatch(schedule, self.battery)
        metrics = summarize("test", rows)
        expected = PUE * (
            sum(SYNTHETIC_INPUTS.protected_it_kw) + sum(job.compute_kwh for job in JOBS)
        )
        self.assertAlmostEqual(metrics["facility_energy_kwh"], expected)
        self.assertNotAlmostEqual(metrics["grid_energy_kwh"], expected)

    def test_finite_differences_match_dispatch_gradient_in_linear_region(self):
        def evaluate(point):
            return objective({"tiny": list(point)}, self.battery, 0.2, self.inputs)

        gradient = finite_difference_gradient(evaluate, np.array([4.0, 6.0]))
        np.testing.assert_allclose(gradient, [PUE * (0.9 + 0.2 * 0.5),
                                             PUE * (0.1 + 0.2 * 0.5)], atol=1e-8)
        boundary = finite_difference_gradient(evaluate, np.array([0.0, 10.0]))
        np.testing.assert_allclose(boundary, gradient, atol=1e-8)

    def test_one_sided_gradient_at_supply_boundary(self):
        def limited(point):
            if point[0] > 1:
                return None
            return 2 * point[0]
        np.testing.assert_allclose(
            finite_difference_gradient(limited, np.array([1.0])), [2.0], atol=1e-8
        )

    def test_random_initialization_is_reproducible(self):
        first = optimize_schedule(
            self.jobs, self.battery, self.inputs, initialization="random", seed=42
        )
        second = optimize_schedule(
            self.jobs, self.battery, self.inputs, initialization="random", seed=42
        )
        self.assertEqual(first, second)

    def test_initialization_methods_and_iteration_limit(self):
        for initialization in ("immediate", "greedy", "uniform", "random"):
            schedule, history = optimize_schedule(
                self.jobs, self.battery, self.inputs,
                initialization=initialization, max_iterations=1,
            )
            verify_schedule(self.jobs, schedule, self.inputs)
            self.assertLessEqual(len(history), 2)
            self.assertIn(history[-1]["status"], {"max_iterations", "converged"})

    def test_infeasible_joint_computing_constraints(self):
        jobs = [Job("one", 0, 1, 20), Job("two", 0, 1, 20)]
        with self.assertRaisesRegex(ValueError, "Infeasible job energies/windows"):
            optimize_schedule(jobs, self.battery, self.inputs)

    def test_shortage_does_not_masquerade_as_global_infeasibility(self):
        inputs = write_bundle(
            self.path, availability={"ordinary_grid": [12, 100]}
        )
        with self.assertRaisesRegex(ValueError, "not proof of global infeasibility"):
            optimize_schedule(self.jobs, self.battery, inputs)
        # A feasible schedule exists despite the failed initialization.
        dispatch({"tiny": [0.0, 10.0]}, self.battery, inputs)

    def test_line_search_rejects_supply_infeasible_steps(self):
        inputs = write_bundle(
            self.path, availability={"ordinary_grid": [18, 100]},
            prices=[_price_row(GRID, "2024-12", 0.1),
                    _price_row(GRID, "2025-01", 0.9)],
        )
        schedule, history = optimize_schedule(
            self.jobs, self.battery, inputs, learning_rate=10, max_iterations=3
        )
        dispatch(schedule, self.battery, inputs)
        self.assertTrue(any(row["rejected_supply_trials"] > 0 for row in history))
        self.assertLessEqual(schedule["tiny"][0], 5 + 1e-6)

    def test_empty_jobs_and_zero_energy_jobs(self):
        for jobs in ([], [Job("zero", 0, 2, 0)]):
            schedule, history = optimize_schedule(jobs, self.battery, self.inputs)
            verify_schedule(jobs, schedule, self.inputs)
            self.assertEqual(history[-1]["status"], "no_flexible_energy")

    def test_invalid_optimizer_parameters(self):
        for kwargs in (
            {"learning_rate": 0}, {"learning_rate": float("inf")},
            {"tolerance": -1}, {"max_iterations": 0}, {"max_iterations": 1.5},
            {"carbon_weight": float("nan")}, {"annual_demand_reference_kwh": 0},
            {"finite_difference_step": 0}, {"initialization": "unknown"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                optimize_schedule(self.jobs, self.battery, self.inputs, **kwargs)

    def test_invalid_jobs_battery_and_normalization(self):
        for jobs in ([Job("bad", 0, 3, 10)], [Job("bad", 0, 2, float("nan"))]):
            with self.assertRaises(ValueError):
                optimize_schedule(jobs, self.battery, self.inputs)
        for battery in (Battery(charge_efficiency=0), Battery(capacity_kwh=-1)):
            with self.assertRaises(ValueError):
                optimize_schedule(self.jobs, battery, self.inputs)
        with self.assertRaisesRegex(ValueError, "zero facility"):
            summarize("empty", [{"facility_kwh": 0}])

    def test_input_accounting_errors_are_not_swallowed(self):
        prices = dict(self.inputs.monthly_prices)
        key = ("ordinary_grid", "2024-12")
        prices[key] = replace(prices[key], price=None)
        inputs = replace(self.inputs, monthly_prices=prices)
        with self.assertRaisesRegex(ValueError, "Missing ordinary_grid price"):
            optimize_schedule(self.jobs, self.battery, inputs)

    def test_scenario_and_source_metadata_are_preserved(self):
        inputs = load_energy_inputs(
            Path(__file__).resolve().parents[1] / "examples" / "synthetic"
        )
        # Ordinary is always valid; an unconfigured scenario remains an error.
        with self.assertRaisesRegex(ValueError, "Unknown energy scenario"):
            optimize_schedule(JOBS, self.battery, inputs, scenario="unknown")
        schedule, _ = optimize_schedule(JOBS, self.battery, inputs, max_iterations=1)
        rows = dispatch(schedule, self.battery, inputs)
        for row in rows:
            for source_id in inputs.sources:
                self.assertIn(f"source_{source_id}_price_status", row)
                self.assertIn(f"source_{source_id}_emission_boundary", row)

    def test_cli_exports_hourly_history_and_comparison(self):
        output = self.path / "results"
        main(["--max-iterations", "1", "--output-dir", str(output)])
        with (output / "comparison.csv").open() as file:
            comparison = list(csv.DictReader(file))
        self.assertEqual(len(comparison), 4)
        self.assertTrue(all(row["feasible"] == "True" for row in comparison))
        self.assertEqual(len({round(float(row["facility_energy_kwh"]), 6)
                              for row in comparison}), 1)
        self.assertIn("score_per_1twh_rmb", comparison[-1])
        with (output / "gradient_hourly.csv").open() as file:
            hourly = list(csv.DictReader(file))
        self.assertEqual(len(hourly), 24)
        self.assertIn("job_training_A_kwh", hourly[0])
        self.assertIn("source_onsite_solar_emission_boundary", hourly[0])
        with (output / "optimization_history.csv").open() as file:
            history = list(csv.DictReader(file))
        self.assertEqual([row["iteration"] for row in history], ["0", "1"])


if __name__ == "__main__":
    unittest.main()
