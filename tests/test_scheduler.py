import csv
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from energy_data import (
    load_emission_factors,
    load_energy_inputs,
    load_monthly_prices,
    load_sources,
)
from energy_data import fuel_litres_to_electricity_kwh, fuel_price_rmb_per_kwh
from frontier import (
    calendar_year_frontiers,
    pareto_frontier,
    write_frontier_csv,
    write_frontier_svg,
)
from scheduler import (
    Battery,
    JOBS,
    Job,
    SYNTHETIC_INPUTS,
    build_schedule,
    dispatch,
    verify_schedule,
)


PRICE_FIELDS = [
    "source_id", "month", "price", "unit", "price_category", "status",
    "publisher", "source_url", "document_title", "publication_date",
    "applicable_period", "geography", "original_units", "method", "notes",
]
FACTOR_FIELDS = [
    "source_id", "factor", "unit", "boundary", "reference_year", "geography",
    "status", "publisher", "source_url", "document_title", "publication_date",
    "applicable_period", "method", "notes",
]
SOURCE_FIELDS = [
    "source_id", "technology", "supply_type", "price_unit", "max_power_kw",
    "dispatch_order", "fuel_consumption_l_per_kwh", "fuel_inventory_l",
]
AVAILABILITY_FIELDS = [
    "timestamp", "source_id", "available_kwh", "protected_it_kw",
]
SCENARIO_IMPACT_FIELDS = [
    "scenario", "source_id", "availability_multiplier", "price_multiplier",
    "notes",
]
TIMESTAMPS = ["2024-12-31T23:00:00", "2025-01-01T00:00:00"]
GRID = {
    "source_id": "ordinary_grid",
    "technology": "ordinary_grid",
    "supply_type": "grid",
    "price_unit": "RMB/kWh",
    "max_power_kw": "100",
    "dispatch_order": "100",
    "fuel_consumption_l_per_kwh": "",
    "fuel_inventory_l": "",
}


def _price_row(source, month, price, unit=None, category=None, status=None):
    unit = unit or source["price_unit"]
    category = category or (
        "fuel_price" if unit == "RMB/litre" else "explicit_user_assumption"
    )
    return {
        "source_id": source["source_id"],
        "month": month,
        "price": "" if price is None else str(price),
        "unit": unit,
        "price_category": category,
        "status": status or ("missing" if price is None else "assumption"),
        "publisher": "Test assumption",
        "source_url": "not applicable",
        "document_title": "Test data",
        "publication_date": "2026-10-24",
        "applicable_period": month,
        "geography": "test",
        "original_units": unit,
        "method": "test fixture",
        "notes": "Synthetic test value",
    }


def _factor_row(source, factor, unit=None, boundary=None, status=None):
    unit = unit or (
        "kgCO2/litre"
        if source["price_unit"] == "RMB/litre"
        else "kgCO2/kWh"
    )
    boundary = boundary or (
        "direct_combustion"
        if unit == "kgCO2/litre"
        else "direct_operational"
    )
    return {
        "source_id": source["source_id"],
        "factor": "" if factor is None else str(factor),
        "unit": unit,
        "boundary": boundary,
        "reference_year": "test",
        "geography": "test",
        "status": status or ("missing" if factor is None else "assumption"),
        "publisher": "Test assumption",
        "source_url": "not applicable",
        "document_title": "Test data",
        "publication_date": "2026-10-24",
        "applicable_period": "test",
        "method": "test fixture",
        "notes": "Synthetic test value",
    }


def write_bundle(
    directory,
    sources=None,
    timestamps=TIMESTAMPS,
    availability=None,
    prices=None,
    factors=None,
    scenario_impacts=None,
):
    sources = sources or [GRID]
    months = sorted({timestamp[:7] for timestamp in timestamps})
    availability = availability or {
        source["source_id"]: [100] * len(timestamps) for source in sources
    }
    if prices is None:
        prices = [
            _price_row(source, month, 0.4)
            for source in sources
            for month in months
        ]
    if factors is None:
        factors = [_factor_row(source, 0.5) for source in sources]
    if scenario_impacts is None:
        scenario_impacts = []

    def write(name, fields, rows):
        with (directory / name).open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    write("source_config.csv", SOURCE_FIELDS, sources)
    availability_rows = []
    for hour, timestamp in enumerate(timestamps):
        for source in sources:
            availability_rows.append({
                "timestamp": timestamp,
                "source_id": source["source_id"],
                "available_kwh": availability[source["source_id"]][hour],
                "protected_it_kw": 10,
            })
    write("hourly_availability.csv", AVAILABILITY_FIELDS, availability_rows)
    write("monthly_prices.csv", PRICE_FIELDS, prices)
    write("emission_factors.csv", FACTOR_FIELDS, factors)
    write("scenario_impacts.csv", SCENARIO_IMPACT_FIELDS, scenario_impacts)
    return load_energy_inputs(directory)


class SolarMarketPriceDataTests(unittest.TestCase):
    def test_2025_weighted_fuel_costs_are_transcribed_as_aggregate_assumptions(self):
        path = (
            Path(__file__).parent.parent
            / "data"
            / "estimated_weighted_fuel_cost.csv"
        )
        with path.open(newline="", encoding="utf-8") as file:
            rows = list(csv.DictReader(file))

        expected_prices = [
            1.48, 1.47, 1.45, 1.39, 1.33, 1.33,
            1.34, 1.33, 1.31, 1.30, 1.30, 1.28,
        ]
        self.assertEqual(len(rows), 12)
        for month_number, (row, expected_price) in enumerate(
            zip(rows, expected_prices), start=1
        ):
            with self.subTest(month=row["month"]):
                self.assertEqual(row["month"], f"2025-{month_number:02d}")
                self.assertEqual(float(row["price"]), expected_price)
                self.assertEqual(row["unit"], "RMB/kWh")
                self.assertEqual(row["status"], "assumption")
                self.assertIn("weighting method not provided", row["method"])
                self.assertIn("source-specific", row["notes"])

    def test_2025_wind_prices_are_converted_to_rmb_per_kwh(self):
        path = Path(__file__).parent.parent / "data" / "monthly_prices.csv"
        with path.open(newline="", encoding="utf-8") as file:
            wind_prices = [
                row for row in csv.DictReader(file)
                if row["source_id"] == "wind" and row["month"].startswith("2025-")
            ]

        expected_prices = [
            0.270, 0.230, 0.205, 0.295, 0.295, 0.330,
            0.265, 0.320, 0.250, 0.290, 0.275, 0.170,
        ]
        self.assertEqual(len(wind_prices), 12)
        for month_number, (row, expected_price) in enumerate(
            zip(wind_prices, expected_prices), start=1
        ):
            with self.subTest(month=row["month"]):
                self.assertEqual(row["month"], f"2025-{month_number:02d}")
                self.assertEqual(float(row["price"]), expected_price)
                self.assertEqual(row["unit"], "RMB/kWh")
                self.assertEqual(row["original_units"], "元/MWh")
                self.assertEqual(row["status"], "assumption")
                self.assertEqual(row["document_title"], "Wind price by month (2025)")

    def test_solar_prices_are_three_region_means_in_rmb_per_kwh(self):
        path = Path(__file__).parent.parent / "data" / "monthly_prices.csv"
        with path.open(newline="", encoding="utf-8") as file:
            solar_prices = [
                row for row in csv.DictReader(file)
                if row["source_id"] == "onsite_solar"
                and row["month"].startswith("2025-")
            ]

        expected_prices = [
            0.168333333,
            0.166666667,
            0.164333333,
            0.156,
            0.134666667,
            0.101166667,
            0.119333333,
            0.123666667,
            0.128236667,
            0.135166667,
            0.152666667,
            0.165666667,
        ]
        self.assertEqual(len(solar_prices), 12)
        for month_number, (row, expected_price) in enumerate(
            zip(solar_prices, expected_prices), start=1
        ):
            with self.subTest(month=row["month"]):
                self.assertEqual(row["month"], f"2025-{month_number:02d}")
                self.assertEqual(row["applicable_period"], row["month"])
                self.assertAlmostEqual(float(row["price"]), expected_price)
                self.assertEqual(row["unit"], "RMB/kWh")
                self.assertEqual(row["original_units"], "RMB/MWh")
                self.assertEqual(
                    row["document_title"], "Solar Energy Market Average by Month"
                )
                self.assertEqual(row["status"], "derived")
                self.assertEqual(
                    row["price_category"], "explicit_user_assumption"
                )
                self.assertIn("not independently verified", row["notes"])

    def test_2025_natural_gas_prices_are_transcribed_as_assumptions(self):
        path = Path(__file__).parent.parent / "data" / "monthly_prices.csv"
        with path.open(newline="", encoding="utf-8") as file:
            gas_prices = [
                row for row in csv.DictReader(file)
                if row["source_id"] == "natural_gas_fired"
                and row["month"].startswith("2025-")
            ]

        expected_prices = [0.9676] * 4 + [0.9514] * 8
        self.assertEqual(len(gas_prices), 12)
        for month_number, (row, expected_price) in enumerate(
            zip(gas_prices, expected_prices), start=1
        ):
            with self.subTest(month=row["month"]):
                self.assertEqual(row["month"], f"2025-{month_number:02d}")
                self.assertEqual(row["applicable_period"], row["month"])
                self.assertEqual(float(row["price"]), expected_price)
                self.assertEqual(row["unit"], "RMB/kWh")
                self.assertEqual(row["price_category"], "explicit_user_assumption")
                self.assertEqual(row["status"], "assumption")
                self.assertEqual(
                    row["source_url"],
                    "https://github.com/user-attachments/assets/"
                    "0970dec9-0910-4975-96bb-b03de1f2351c",
                )
                self.assertIn("not independently verified", row["notes"])

    def test_default_energy_template_has_source_types_without_ordinary_grid(self):
        root = Path(__file__).parent.parent
        sources = load_sources(root / "data" / "source_config.csv")
        expected_sources = {
            "onsite_solar",
            "wind",
            "nuclear",
            "natural_gas_fired",
            "onsite_diesel",
        }
        self.assertEqual(set(sources), expected_sources)
        self.assertFalse(any(source.supply_type == "grid" for source in sources.values()))
        timestamps = [
            datetime(year, month, 1)
            for year in (2025, 2026)
            for month in range(1, 13)
        ]
        prices = load_monthly_prices(
            root / "data" / "monthly_prices.csv", sources, timestamps
        )
        factors = load_emission_factors(
            root / "data" / "emission_factors.csv", sources
        )
        self.assertEqual(len(prices), len(expected_sources) * 24)
        self.assertEqual(set(factors), expected_sources)
        self.assertTrue(
            all(
                prices[(source_id, f"2025-{month:02d}")].price is None
                for source_id in expected_sources - {
                    "onsite_solar", "wind", "natural_gas_fired"
                }
                for month in range(1, 13)
            )
        )
        self.assertTrue(
            all(
                prices[(source_id, f"2026-{month:02d}")].price is None
                for source_id in expected_sources - {"onsite_solar", "nuclear"}
                for month in range(1, 13)
            )
        )
        self.assertEqual(
            {source_id: factor.factor for source_id, factor in factors.items()},
            {
                "onsite_solar": 0.041,
                "wind": 0.0115,
                "nuclear": 0.059,
                "natural_gas_fired": 0.440,
                "onsite_diesel": 0.790,
            },
        )
        self.assertTrue(
            all(
                factor.unit == "kgCO2e/kWh"
                and factor.boundary == "lifecycle"
                and factor.status == "assumption"
                for factor in factors.values()
            )
        )

        with (root / "data" / "monthly_prices.csv").open(
            newline="", encoding="utf-8"
        ) as file:
            prices = list(csv.DictReader(file))
        with (root / "data" / "emission_factors.csv").open(
            newline="", encoding="utf-8"
        ) as file:
            factors = list(csv.DictReader(file))
        self.assertEqual(
            {row["source_id"] for row in prices},
            expected_sources,
        )
        self.assertTrue(all(
            row["month"].startswith(("2025-", "2026-")) for row in prices
        ))
        self.assertEqual(
            {row["source_id"] for row in factors},
            expected_sources,
        )
        self.assertTrue(all(
            row["status"] == "missing"
            for row in prices
            if row["source_id"] == "nuclear"
            and row["month"].startswith("2025-")
        ))
        self.assertTrue(all(
            row["status"] == "missing"
            for row in prices
            if row["source_id"] == "onsite_diesel"
        ))
        self.assertTrue(all(row["status"] == "assumption" for row in factors))

    def test_nuclear_price_is_a_constant_monthly_assumption(self):
        path = Path(__file__).parent.parent / "data" / "monthly_prices.csv"
        with path.open(newline="", encoding="utf-8") as file:
            nuclear_prices = [
                row for row in csv.DictReader(file)
                if row["source_id"] == "nuclear"
                and row["month"].startswith("2026-")
            ]

        self.assertEqual(len(nuclear_prices), 12)
        for month_number, row in enumerate(nuclear_prices, start=1):
            with self.subTest(month=row["month"]):
                self.assertEqual(row["month"], f"2026-{month_number:02d}")
                self.assertEqual(float(row["price"]), 0.3932)
                self.assertEqual(row["unit"], "RMB/kWh")
                self.assertEqual(row["status"], "assumption")
                self.assertIn("not independently verified", row["notes"])

    def test_emission_factor_rows_preserve_deepseek_source_urls(self):
        path = Path(__file__).parent.parent / "data" / "emission_factors.csv"
        with path.open(newline="", encoding="utf-8") as file:
            factors = {row["source_id"]: row for row in csv.DictReader(file)}

        self.assertEqual(set(factors), {
            "onsite_solar", "wind", "nuclear", "natural_gas_fired",
            "onsite_diesel",
        })
        self.assertTrue(all(row["publisher"] == "DeepSeek" for row in factors.values()))
        self.assertIn("nrel.gov", factors["natural_gas_fired"]["source_url"])
        self.assertIn("energynewsbeat.co", factors["natural_gas_fired"]["source_url"])
        self.assertIn("icef.go.jp", factors["onsite_diesel"]["source_url"])
        self.assertIn("mendeley.com", factors["wind"]["source_url"])
        self.assertIn("gridreadiness.com", factors["nuclear"]["source_url"])
        self.assertIn("arxiv.org", factors["onsite_solar"]["source_url"])
        self.assertIn(
            "solarpanelsfordatacenters.co.uk",
            factors["onsite_solar"]["source_url"],
        )


class MonthlyEnergyInputTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.directory = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_december_to_january_selects_each_month_price(self):
        timestamps = [
            "2024-12-31T22:00",
            "2024-12-31T23:00",
            "2025-01-01T00:00",
        ]
        inputs = write_bundle(
            self.directory,
            timestamps=timestamps,
            prices=[
                _price_row(GRID, "2024-12", 0.2),
                _price_row(GRID, "2025-01", 0.7),
            ],
        )
        rows = dispatch({}, Battery(), inputs)
        self.assertEqual(rows[0]["timestamp"], "2024-12-31T22:00:00")
        self.assertEqual(rows[1]["timestamp"], "2024-12-31T23:00:00")
        self.assertEqual(rows[2]["timestamp"], "2025-01-01T00:00:00")
        self.assertAlmostEqual(rows[0]["electricity_cost_rmb"], 12 * 0.2)
        self.assertAlmostEqual(rows[1]["electricity_cost_rmb"], 12 * 0.2)
        self.assertAlmostEqual(rows[2]["electricity_cost_rmb"], 12 * 0.7)

    def test_missing_and_duplicate_source_month_records_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing source-month record"):
            write_bundle(
                self.directory,
                prices=[_price_row(GRID, "2025-01", 0.7)],
            )
        duplicate = _price_row(GRID, "2024-12", 0.2)
        with self.assertRaisesRegex(ValueError, "duplicate source-month"):
            write_bundle(
                self.directory,
                prices=[
                    duplicate,
                    _price_row(GRID, "2025-01", 0.7),
                    duplicate,
                ],
            )

    def test_negative_electricity_price_is_allowed(self):
        inputs = write_bundle(
            self.directory,
            prices=[
                _price_row(GRID, "2024-12", -0.2),
                _price_row(GRID, "2025-01", 0.7),
            ],
        )
        self.assertLess(dispatch({}, Battery(), inputs)[0]["electricity_cost_rmb"], 0)

    def test_missing_price_is_not_silently_zero(self):
        inputs = write_bundle(
            self.directory,
            prices=[
                _price_row(GRID, "2024-12", None),
                _price_row(GRID, "2025-01", 0.7),
            ],
        )
        with self.assertRaisesRegex(ValueError, "refusing to use zero"):
            dispatch({}, Battery(), inputs)

    def test_inconsistent_price_and_emission_units_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            write_bundle(
                self.directory,
                prices=[
                    _price_row(GRID, "2024-12", 0.2, unit="RMB/litre"),
                    _price_row(GRID, "2025-01", 0.7),
                ],
            )
        with self.assertRaisesRegex(ValueError, "electricity factor"):
            write_bundle(
                self.directory,
                factors=[
                    _factor_row(GRID, 0.5, unit="kgCO2/litre"),
                ],
            )

    def test_price_categories_are_not_interchangeable(self):
        with self.assertRaisesRegex(ValueError, "generator selling prices apply"):
            write_bundle(
                self.directory,
                prices=[
                    _price_row(
                        GRID, "2024-12", 0.2,
                        category="generator_selling_price",
                    ),
                    _price_row(GRID, "2025-01", 0.7),
                ],
            )

    def test_non_numeric_and_non_finite_values_are_rejected(self):
        for invalid in ("cheap", "nan", "inf"):
            with self.subTest(value=invalid):
                with self.assertRaisesRegex(ValueError, "non-numeric|non-finite"):
                    write_bundle(
                        self.directory,
                        prices=[
                            _price_row(GRID, "2024-12", invalid),
                            _price_row(GRID, "2025-01", 0.7),
                        ],
                    )

    def test_negative_emissions_and_availability_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "negative factor"):
            write_bundle(
                self.directory,
                factors=[_factor_row(GRID, -0.5)],
            )
        with self.assertRaisesRegex(ValueError, "negative available_kwh"):
            write_bundle(
                self.directory,
                availability={"ordinary_grid": [-1, 100]},
            )

    def test_hourly_timestamps_must_be_contiguous(self):
        with self.assertRaisesRegex(ValueError, "one hour at a time"):
            write_bundle(
                self.directory,
                timestamps=["2025-01-01T00:00", "2025-01-01T02:00"],
            )

    def test_missing_price_factor_status_cannot_have_numeric_value(self):
        with self.assertRaisesRegex(ValueError, "missing prices must have a blank"):
            write_bundle(
                self.directory,
                prices=[
                    _price_row(GRID, "2024-12", 0.2, status="missing"),
                    _price_row(GRID, "2025-01", 0.7),
                ],
            )

    def test_diesel_price_and_emissions_convert_via_litres_per_kwh(self):
        diesel = {
            "source_id": "onsite_diesel",
            "technology": "diesel_generator",
            "supply_type": "onsite",
            "price_unit": "RMB/litre",
            "max_power_kw": "10",
            "dispatch_order": "1",
            "fuel_consumption_l_per_kwh": "0.25",
            "fuel_inventory_l": "4",
        }
        grid = dict(GRID, dispatch_order="2")
        inputs = write_bundle(
            self.directory,
            sources=[diesel, grid],
            availability={
                "onsite_diesel": [10, 10],
                "ordinary_grid": [100, 100],
            },
            prices=[
                _price_row(diesel, "2024-12", 8.0),
                _price_row(diesel, "2025-01", 8.0),
                _price_row(grid, "2024-12", 0.6),
                _price_row(grid, "2025-01", 0.6),
            ],
            factors=[
                _factor_row(diesel, 2.68),
                _factor_row(grid, 0.5),
            ],
        )
        rows = dispatch({}, Battery(), inputs)
        self.assertEqual(sum(row["source_onsite_diesel_kwh"] for row in rows), 16)
        self.assertAlmostEqual(
            sum(row["source_onsite_diesel_cost_rmb"] for row in rows), 32
        )
        self.assertAlmostEqual(
            sum(row["source_onsite_diesel_emissions_kg"] for row in rows), 10.72
        )
        self.assertAlmostEqual(
            rows[0]["source_onsite_diesel_effective_price_rmb_per_kwh"], 2
        )
        self.assertAlmostEqual(
            sum(row["source_ordinary_grid_kwh"] for row in rows), 8
        )

    def test_fuel_volume_converts_to_electricity_and_invalid_rates_fail(self):
        self.assertEqual(fuel_litres_to_electricity_kwh(4, 0.25), 16)
        self.assertEqual(fuel_price_rmb_per_kwh(8, 0.25), 2)
        with self.assertRaisesRegex(ValueError, "consumption positive"):
            fuel_litres_to_electricity_kwh(4, 0)

    def test_scenario_file_is_optional_for_existing_input_bundles(self):
        write_bundle(self.directory)
        (self.directory / "scenario_impacts.csv").unlink()
        inputs = load_energy_inputs(self.directory)
        self.assertEqual(inputs.scenario_impacts, {})
        self.assertEqual(len(dispatch({}, Battery(), inputs)), 2)

    def test_negative_diesel_consumption_is_rejected(self):
        diesel = {
            "source_id": "onsite_diesel",
            "technology": "diesel_generator",
            "supply_type": "onsite",
            "price_unit": "RMB/litre",
            "max_power_kw": "10",
            "dispatch_order": "1",
            "fuel_consumption_l_per_kwh": "-0.25",
            "fuel_inventory_l": "4",
        }
        with self.assertRaisesRegex(ValueError, "negative fuel_consumption"):
            write_bundle(self.directory, sources=[diesel, GRID])

    def test_battery_energy_balance_and_solar_night_availability(self):
        solar = {
            "source_id": "onsite_solar",
            "technology": "solar_pv",
            "supply_type": "onsite",
            "price_unit": "RMB/kWh",
            "max_power_kw": "20",
            "dispatch_order": "1",
            "fuel_consumption_l_per_kwh": "",
            "fuel_inventory_l": "",
        }
        grid = dict(GRID, dispatch_order="2")
        inputs = write_bundle(
            self.directory,
            timestamps=["2025-01-01T00:00", "2025-01-01T01:00"],
            sources=[solar, grid],
            availability={
                "onsite_solar": [20, 0],
                "ordinary_grid": [100, 100],
            },
            prices=[
                _price_row(solar, "2025-01", 0),
                _price_row(grid, "2025-01", 0.4),
            ],
            factors=[
                _factor_row(solar, 0),
                _factor_row(grid, 0.5),
            ],
        )
        rows = dispatch({}, Battery(10, 10, 0.95, 0.95), inputs)
        self.assertEqual(rows[1]["solar_available_kwh"], 0)
        self.assertAlmostEqual(rows[0]["battery_charge_kwh"], 8)
        self.assertAlmostEqual(rows[1]["battery_discharge_kwh"], 7.22)
        supply = sum(
            row["source_onsite_solar_kwh"] + row["source_ordinary_grid_kwh"]
            for row in rows
        )
        demand = sum(row["facility_kwh"] for row in rows)
        losses = sum(
            row["battery_charge_kwh"] * (1 - 0.95)
            + row["battery_discharge_kwh"] * (1 / 0.95 - 1)
            for row in rows
        )
        self.assertAlmostEqual(supply, demand + losses)

    def test_grid_and_source_contract_are_not_double_counted(self):
        gas = {
            "source_id": "gas_contract",
            "technology": "natural_gas_fired",
            "supply_type": "contract",
            "price_unit": "RMB/kWh",
            "max_power_kw": "3",
            "dispatch_order": "1",
            "fuel_consumption_l_per_kwh": "",
            "fuel_inventory_l": "",
        }
        grid = dict(GRID, dispatch_order="2")
        inputs = write_bundle(
            self.directory,
            sources=[gas, grid],
            availability={
                "gas_contract": [3, 3],
                "ordinary_grid": [100, 100],
            },
            prices=[
                _price_row(gas, "2024-12", 0.4),
                _price_row(gas, "2025-01", 0.4),
                _price_row(grid, "2024-12", 0.5),
                _price_row(grid, "2025-01", 0.5),
            ],
            factors=[
                _factor_row(gas, 0.8),
                _factor_row(grid, 0.5),
            ],
        )
        rows = dispatch({}, Battery(), inputs)
        self.assertEqual(rows[0]["source_gas_contract_kwh"], 3)
        self.assertEqual(rows[0]["grid_kwh"], 9)
        self.assertEqual(
            rows[0]["source_gas_contract_kwh"] + rows[0]["grid_kwh"], 12
        )

    def test_grid_generation_mix_cannot_be_configured_as_grid_supply(self):
        generation_grid = dict(GRID, source_id="wind", technology="wind")
        with self.assertRaisesRegex(ValueError, "Only ordinary_grid"):
            write_bundle(self.directory, sources=[generation_grid])

    def test_grid_outage_uses_strategic_diesel_reserve(self):
        diesel = {
            "source_id": "diesel_reserve",
            "technology": "diesel_generator",
            "supply_type": "onsite",
            "price_unit": "RMB/litre",
            "max_power_kw": "20",
            "dispatch_order": "100",
            "fuel_consumption_l_per_kwh": "0.25",
            "fuel_inventory_l": "6",
        }
        grid = dict(GRID, max_power_kw="100", dispatch_order="1")
        inputs = write_bundle(
            self.directory,
            sources=[grid, diesel],
            availability={"ordinary_grid": [100, 100], "diesel_reserve": [20, 20]},
            scenario_impacts=[
                {
                    "scenario": "grid_outage",
                    "source_id": "ordinary_grid",
                    "availability_multiplier": "0",
                    "price_multiplier": "1",
                    "notes": "Grid unavailable; deploy reserve.",
                },
            ],
        )
        rows = dispatch({}, Battery(), inputs, scenario="grid_outage")
        self.assertEqual(sum(row["grid_kwh"] for row in rows), 0)
        self.assertAlmostEqual(
            sum(row["source_diesel_reserve_kwh"] for row in rows), 24
        )
        with self.assertRaisesRegex(ValueError, "Unknown energy scenario"):
            dispatch({}, Battery(), inputs, scenario="unconfigured")

    def test_concentrated_gas_supply_shock_changes_only_configured_source(self):
        gas = dict(
            GRID,
            source_id="gas_import",
            technology="natural_gas_fired",
            supply_type="contract",
            max_power_kw="100",
            dispatch_order="1",
        )
        grid = dict(GRID, dispatch_order="2")
        inputs = write_bundle(
            self.directory,
            sources=[gas, grid],
            availability={"gas_import": [12, 12], "ordinary_grid": [100, 100]},
            scenario_impacts=[
                {
                    "scenario": "gas_supply_shock",
                    "source_id": "gas_import",
                    "availability_multiplier": "0.2",
                    "price_multiplier": "1.5",
                    "notes": "Illustrative shock for a concentrated import source.",
                },
            ],
        )
        rows = dispatch({}, Battery(), inputs, scenario="gas_supply_shock")
        self.assertAlmostEqual(rows[0]["source_gas_import_kwh"], 2.4)
        self.assertAlmostEqual(rows[0]["source_gas_import_cost_rmb"], 1.44)
        self.assertAlmostEqual(rows[0]["source_ordinary_grid_kwh"], 9.6)

    def test_all_documented_electricity_technologies_are_configurable(self):
        technologies = [
            ("onsite_solar", "solar_pv", "onsite"),
            ("nuclear_contract", "nuclear", "contract"),
            ("gas_contract", "natural_gas_fired", "contract"),
            ("wind_contract", "wind", "contract"),
            ("ordinary_grid", "ordinary_grid", "grid"),
        ]
        sources = [
            {
                "source_id": source_id,
                "technology": technology,
                "supply_type": supply_type,
                "price_unit": "RMB/kWh",
                "max_power_kw": "100",
                "dispatch_order": str(index),
                "fuel_consumption_l_per_kwh": "",
                "fuel_inventory_l": "",
            }
            for index, (source_id, technology, supply_type)
            in enumerate(technologies)
        ]
        inputs = write_bundle(
            self.directory,
            sources=sources,
            availability={
                source["source_id"]: (
                    [100, 100]
                    if source["supply_type"] == "grid"
                    else [0, 0]
                )
                for source in sources
            },
        )
        self.assertEqual(set(inputs.sources), {item[0] for item in technologies})

    def test_direct_and_lifecycle_emissions_cannot_be_combined(self):
        solar = {
            "source_id": "onsite_solar",
            "technology": "solar_pv",
            "supply_type": "onsite",
            "price_unit": "RMB/kWh",
            "max_power_kw": "10",
            "dispatch_order": "1",
            "fuel_consumption_l_per_kwh": "",
            "fuel_inventory_l": "",
        }
        grid = dict(GRID, dispatch_order="2")
        inputs = write_bundle(
            self.directory,
            sources=[solar, grid],
            availability={
                "onsite_solar": [3, 3],
                "ordinary_grid": [100, 100],
            },
            prices=[
                _price_row(solar, "2024-12", 0),
                _price_row(solar, "2025-01", 0),
                _price_row(grid, "2024-12", 0.5),
                _price_row(grid, "2025-01", 0.5),
            ],
            factors=[
                _factor_row(solar, 0),
                _factor_row(
                    grid, 0.6, unit="kgCO2e/kWh", boundary="lifecycle"
                ),
            ],
        )
        with self.assertRaisesRegex(ValueError, "Cannot combine lifecycle"):
            dispatch({}, Battery(), inputs)

    def test_job_deadlines_and_capacity_constraints_remain_enforced(self):
        inputs = write_bundle(
            self.directory,
            timestamps=["2025-01-01T00:00", "2025-01-01T01:00"],
        )
        with self.assertRaisesRegex(ValueError, "Could not place"):
            build_schedule([Job("too_large", 0, 2, 60)], Battery(), inputs=inputs)
        with self.assertRaisesRegex(ValueError, "Invalid time window"):
            build_schedule([Job("late", 0, 3, 1)], Battery(), inputs=inputs)

        schedule = build_schedule(
            [Job("small", 0, 2, 10)], Battery(), inputs=inputs
        )
        verify_schedule([Job("small", 0, 2, 10)], schedule, inputs)

    def test_builtin_synthetic_data_is_explicit_and_schedulable(self):
        self.assertEqual(len(SYNTHETIC_INPUTS.timestamps), 24)
        self.assertEqual(SYNTHETIC_INPUTS.availability_kwh["onsite_solar"][0], 0)
        schedule = build_schedule(JOBS, Battery())
        verify_schedule(JOBS, schedule)
        self.assertEqual(len(dispatch(schedule, Battery())), 24)

    def test_frontier_excludes_dominated_supply_options(self):
        wind = dict(
            GRID,
            source_id="wind_contract",
            technology="wind",
            supply_type="contract",
            dispatch_order="1",
        )
        dominated = dict(
            GRID,
            source_id="high_cost_high_emissions",
            technology="natural_gas_fired",
            supply_type="contract",
            dispatch_order="2",
            max_power_kw="1000",
        )
        wind["max_power_kw"] = "1000"
        grid = dict(GRID, dispatch_order="3", max_power_kw="1000")
        sources = [wind, dominated, grid]
        inputs = write_bundle(
            self.directory,
            sources=sources,
            availability={
                source["source_id"]: [1000, 1000] for source in sources
            },
            prices=[
                _price_row(source, month, price)
                for source, price in (
                    (wind, 0.8),
                    (dominated, 0.9),
                    (grid, 0.5),
                )
                for month in ("2024-12", "2025-01")
            ],
            factors=[
                _factor_row(wind, 0.2),
                _factor_row(dominated, 0.9),
                _factor_row(grid, 0.8),
            ],
        )
        points, unit = pareto_frontier(inputs, target_mwh=1)
        self.assertEqual(unit, "kgCO2/kWh")
        self.assertEqual(len(points), 2)
        self.assertEqual(
            {point["mix"] for point in points},
            {
                "ordinary_grid=100.0%",
                "wind_contract=100.0%",
            },
        )
        self.assertEqual(
            {point["emissions_kg_per_mwh"] for point in points},
            {200.0, 800.0},
        )

    def test_synthetic_frontier_respects_hourly_supply_limit(self):
        points, _ = pareto_frontier(SYNTHETIC_INPUTS, target_mwh=1)
        self.assertEqual(len(points), 1)
        self.assertIn("onsite_solar=", points[0]["mix"])
        self.assertIn("ordinary_grid=", points[0]["mix"])
        self.assertAlmostEqual(points[0]["energy_kwh"], 1000)
        self.assertAlmostEqual(
            points[0]["emissions_kg_per_mwh"],
            points[0]["avg_price_rmb_per_kwh"] / 0.58 * 550,
        )

    def test_frontier_fails_if_configured_sources_cannot_supply_target(self):
        with self.assertRaisesRegex(ValueError, "less than the"):
            pareto_frontier(SYNTHETIC_INPUTS, target_mwh=100)

    def test_frontier_normalizes_diesel_emissions_to_kgco2(self):
        diesel = {
            "source_id": "onsite_diesel",
            "technology": "diesel_generator",
            "supply_type": "onsite",
            "price_unit": "RMB/litre",
            "max_power_kw": "1000",
            "dispatch_order": "1",
            "fuel_consumption_l_per_kwh": "0.25",
            "fuel_inventory_l": "1000",
        }
        grid = dict(GRID, max_power_kw="1000")
        inputs = write_bundle(
            self.directory,
            sources=[diesel, grid],
            availability={
                "onsite_diesel": [1000, 1000],
                "ordinary_grid": [1000, 1000],
            },
            prices=[
                _price_row(diesel, month, 1.0)
                for month in ("2024-12", "2025-01")
            ] + [
                _price_row(grid, month, 0.5)
                for month in ("2024-12", "2025-01")
            ],
            factors=[
                _factor_row(diesel, 2.68),
                _factor_row(grid, 0.5),
            ],
        )
        points, unit = pareto_frontier(inputs, target_mwh=1)
        self.assertEqual(unit, "kgCO2/kWh")
        self.assertEqual(len(points), 2)
        self.assertIn(670.0, {point["emissions_kg_per_mwh"] for point in points})

    def test_frontier_csv_and_svg_outputs_are_descriptive(self):
        points, unit = pareto_frontier(SYNTHETIC_INPUTS, target_mwh=1)
        csv_path = self.directory / "frontier.csv"
        svg_path = self.directory / "frontier.svg"
        write_frontier_csv(csv_path, points, unit)
        write_frontier_svg(svg_path, points, unit, 1)
        csv_content = csv_path.read_text(encoding="utf-8")
        svg_content = svg_path.read_text(encoding="utf-8")
        self.assertIn("avg_price_rmb_per_kwh", csv_content)
        self.assertIn("emissions_kgCO2_per_MWh", csv_content)
        self.assertIn("<svg", svg_content)
        self.assertIn("Average price (RMB/kWh)", svg_content)
        self.assertIn("Emissions (kgCO2/MWh)", svg_content)

    def test_annual_frontier_plot_keeps_years_separate_and_ignores_scenarios(self):
        inputs = write_bundle(
            self.directory,
            timestamps=["2024-12-31T23:00", "2025-01-01T00:00"],
            prices=[
                _price_row(GRID, "2024-12", 0.2),
                _price_row(GRID, "2025-01", 0.7),
            ],
            scenario_impacts=[
                {
                    "scenario": "grid_outage",
                    "source_id": "ordinary_grid",
                    "availability_multiplier": "0",
                    "price_multiplier": "10",
                    "notes": "Should not affect annual procurement frontier.",
                },
            ],
        )
        points, unit = calendar_year_frontiers(inputs, target_mwh=0.1)
        self.assertEqual(unit, "kgCO2/kWh")
        self.assertEqual([point["year"] for point in points], [2024, 2025])
        self.assertAlmostEqual(points[0]["avg_price_rmb_per_kwh"], 0.2)
        self.assertAlmostEqual(points[1]["avg_price_rmb_per_kwh"], 0.7)
        self.assertAlmostEqual(points[0]["cost_rmb"], 20)
        self.assertAlmostEqual(points[1]["cost_rmb"], 70)

        csv_path = self.directory / "annual_frontier.csv"
        svg_path = self.directory / "annual_frontier.svg"
        write_frontier_csv(csv_path, points, unit, include_year=True)
        write_frontier_svg(svg_path, points, unit, 0.1, series_key="year")
        self.assertIn("year,cost_rmb,energy_kwh", csv_path.read_text(encoding="utf-8"))
        svg = svg_path.read_text(encoding="utf-8")
        self.assertIn(">2024</text>", svg)
        self.assertIn(">2025</text>", svg)
        self.assertIn("Procurement cost (RMB for 0.1 MWh)", svg)


if __name__ == "__main__":
    unittest.main()
