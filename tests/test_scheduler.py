import csv
from pathlib import Path
import tempfile
import unittest

from scheduler import (
    Battery,
    JOBS,
    SYNTHETIC_INPUTS,
    build_schedule,
    load_hourly_inputs,
    verify_schedule,
)


ROOT = Path(__file__).resolve().parents[1]
COLUMNS = [
    "hour",
    "price_rmb_per_kwh",
    "solar_kw",
    "grid_co2_kg_per_kwh",
    "protected_it_kw",
]


def valid_rows():
    return [
        [hour, 0.3, 1, 0.5, 10]
        for hour in range(24)
    ]


class LoadHourlyInputsTests(unittest.TestCase):
    def load_rows(self, rows, columns=COLUMNS):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inputs.csv"
            with path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(columns)
                writer.writerows(rows)
            return load_hourly_inputs(path)

    def test_synthetic_example_matches_builtin_data(self):
        inputs = load_hourly_inputs(ROOT / "examples" / "synthetic_inputs.csv")
        self.assertEqual(inputs.price, SYNTHETIC_INPUTS.price)
        self.assertEqual(inputs.solar_kw, SYNTHETIC_INPUTS.solar_kw)
        self.assertEqual(
            inputs.grid_co2_kg_per_kwh,
            SYNTHETIC_INPUTS.grid_co2_kg_per_kwh,
        )
        self.assertEqual(
            inputs.protected_it_kw, SYNTHETIC_INPUTS.protected_it_kw
        )

    def test_rows_are_sorted_by_hour_and_negative_prices_are_allowed(self):
        rows = valid_rows()
        rows[0][1] = -0.2
        inputs = self.load_rows(rows[::-1])
        self.assertEqual(inputs.price[0], -0.2)
        self.assertEqual(inputs.price[23], 0.3)

    def test_missing_required_column_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            self.load_rows(valid_rows(), COLUMNS[:-1])

    def test_duplicate_hour_is_rejected(self):
        rows = valid_rows()
        rows[1][0] = 0
        with self.assertRaisesRegex(ValueError, "duplicate hour 0"):
            self.load_rows(rows)

    def test_missing_hour_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing hours: 23"):
            self.load_rows(valid_rows()[:-1])

    def test_missing_value_is_rejected(self):
        rows = valid_rows()
        rows[0][1] = ""
        with self.assertRaisesRegex(ValueError, "missing value"):
            self.load_rows(rows)

    def test_non_numeric_value_is_rejected(self):
        rows = valid_rows()
        rows[0][1] = "cheap"
        with self.assertRaisesRegex(ValueError, "non-numeric"):
            self.load_rows(rows)

    def test_negative_solar_carbon_and_protected_demand_are_rejected(self):
        for column_index, column_name in (
            (2, "solar_kw"),
            (3, "grid_co2_kg_per_kwh"),
            (4, "protected_it_kw"),
        ):
            with self.subTest(column=column_name):
                rows = valid_rows()
                rows[0][column_index] = -1
                with self.assertRaisesRegex(ValueError, column_name):
                    self.load_rows(rows)

    def test_csv_inputs_are_used_by_scheduler(self):
        inputs = self.load_rows(valid_rows())
        schedule = build_schedule(JOBS, Battery(), inputs=inputs)
        verify_schedule(JOBS, schedule, inputs)


if __name__ == "__main__":
    unittest.main()
