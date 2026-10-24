from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
import csv
import math
from pathlib import Path
import re


PRICE_COLUMNS = {
    "source_id",
    "month",
    "price",
    "unit",
    "price_category",
    "status",
    "publisher",
    "source_url",
    "document_title",
    "publication_date",
    "applicable_period",
    "geography",
    "original_units",
    "method",
    "notes",
}
EMISSION_COLUMNS = {
    "source_id",
    "factor",
    "unit",
    "boundary",
    "reference_year",
    "geography",
    "status",
    "publisher",
    "source_url",
    "document_title",
    "publication_date",
    "applicable_period",
    "method",
    "notes",
}
SOURCE_COLUMNS = {
    "source_id",
    "technology",
    "supply_type",
    "price_unit",
    "max_power_kw",
    "dispatch_order",
    "fuel_consumption_l_per_kwh",
    "fuel_inventory_l",
}
AVAILABILITY_COLUMNS = {
    "timestamp",
    "source_id",
    "available_kwh",
    "protected_it_kw",
}
SCENARIO_IMPACT_COLUMNS = {
    "scenario",
    "source_id",
    "availability_multiplier",
    "price_multiplier",
    "notes",
}
PRICE_CATEGORIES = {
    "customer_purchase_price",
    "generator_selling_price",
    "fuel_price",
    "estimated_generation_cost_lcoe",
    "explicit_user_assumption",
}
STATUSES = {"observed", "derived", "assumption", "missing"}


@dataclass(frozen=True)
class EnergySource:
    source_id: str
    technology: str
    supply_type: str
    price_unit: str
    max_power_kw: float
    dispatch_order: int
    fuel_consumption_l_per_kwh: float | None
    fuel_inventory_l: float | None


@dataclass(frozen=True)
class MonthlyPrice:
    source_id: str
    month: str
    price: float | None
    unit: str
    price_category: str
    status: str


@dataclass(frozen=True)
class EmissionFactor:
    source_id: str
    factor: float | None
    unit: str
    boundary: str
    reference_year: str
    status: str


@dataclass(frozen=True)
class ScenarioImpact:
    availability_multiplier: float
    price_multiplier: float


@dataclass(frozen=True)
class EnergyInputs:
    timestamps: tuple
    protected_it_kw: tuple
    availability_kwh: dict
    sources: dict
    monthly_prices: dict
    emission_factors: dict
    scenario_impacts: dict = field(default_factory=dict)


def fuel_litres_to_electricity_kwh(fuel_litres, fuel_consumption_l_per_kwh):
    """Convert reserve fuel volume to output using the generator's own rate."""
    if (
        not math.isfinite(fuel_litres)
        or fuel_litres < 0
        or not math.isfinite(fuel_consumption_l_per_kwh)
        or fuel_consumption_l_per_kwh <= 0
    ):
        raise ValueError("Fuel litres must be non-negative and consumption positive.")
    return fuel_litres / fuel_consumption_l_per_kwh


def fuel_price_rmb_per_kwh(price_rmb_per_litre, fuel_consumption_l_per_kwh):
    """Express fuel cost per litre as the generator's equivalent cost per kWh."""
    if (
        not math.isfinite(price_rmb_per_litre)
        or price_rmb_per_litre < 0
        or not math.isfinite(fuel_consumption_l_per_kwh)
        or fuel_consumption_l_per_kwh <= 0
    ):
        raise ValueError("Fuel price must be non-negative and consumption positive.")
    return price_rmb_per_litre * fuel_consumption_l_per_kwh


def _read_csv(path, required_columns):
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        headers = reader.fieldnames
        if headers is None:
            raise ValueError(f"{path} is missing a header row.")
        if len(headers) != len(set(headers)):
            raise ValueError(f"{path} has duplicate column names.")
        missing = sorted(required_columns - set(headers))
        if missing:
            raise ValueError(
                f"{path} is missing required columns: {', '.join(missing)}"
            )
        rows = []
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"{path} row {line_number} has too many values.")
            rows.append((line_number, row))
        return rows


def _required(row, column, path, line_number):
    value = row.get(column)
    if value is None or not value.strip():
        raise ValueError(
            f"{path} row {line_number} is missing a value for {column}."
        )
    return value.strip()


def _number(value, column, path, line_number, allow_negative=False):
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{path} row {line_number} has a non-numeric {column}: {value!r}."
        ) from error
    if not math.isfinite(result):
        raise ValueError(
            f"{path} row {line_number} has a non-finite {column}."
        )
    if not allow_negative and result < 0:
        raise ValueError(
            f"{path} row {line_number} has a negative {column}."
        )
    return result


def load_sources(path):
    rows = _read_csv(path, SOURCE_COLUMNS)
    sources = {}
    for line_number, row in rows:
        source_id = _required(row, "source_id", path, line_number)
        if source_id in sources:
            raise ValueError(f"{path} contains duplicate source {source_id!r}.")
        supply_type = _required(row, "supply_type", path, line_number)
        if supply_type not in {"onsite", "contract", "grid"}:
            raise ValueError(
                f"{path} row {line_number} has invalid supply_type "
                f"{supply_type!r}."
            )
        technology = _required(row, "technology", path, line_number)
        if (technology == "ordinary_grid") != (supply_type == "grid"):
            raise ValueError(
                "Only ordinary_grid may use supply_type=grid; generation "
                "technologies must be configured as onsite or contract supply."
            )
        price_unit = _required(row, "price_unit", path, line_number)
        if price_unit not in {"RMB/kWh", "RMB/litre"}:
            raise ValueError(f"{path} row {line_number} has invalid price_unit.")
        if (price_unit == "RMB/litre") != (
            row.get("fuel_consumption_l_per_kwh", "").strip() != ""
        ):
            raise ValueError(
                f"{path} row {line_number} must configure a fuel consumption "
                "rate only for fuel-priced sources."
            )
        if price_unit == "RMB/litre" and (
            technology != "diesel_generator" or supply_type != "onsite"
        ):
            raise ValueError(
                "RMB/litre pricing is supported only for an onsite "
                "diesel_generator."
            )
        consumption = (
            _number(
                row["fuel_consumption_l_per_kwh"],
                "fuel_consumption_l_per_kwh",
                path,
                line_number,
            )
            if row.get("fuel_consumption_l_per_kwh", "").strip()
            else None
        )
        if price_unit == "RMB/litre" and consumption == 0:
            raise ValueError("Diesel fuel consumption must be greater than zero.")
        inventory = (
            _number(row["fuel_inventory_l"], "fuel_inventory_l", path, line_number)
            if row.get("fuel_inventory_l", "").strip()
            else None
        )
        if price_unit == "RMB/litre" and inventory is None:
            raise ValueError("Diesel sources require a configured fuel inventory.")
        if price_unit != "RMB/litre" and inventory is not None:
            raise ValueError(
                f"{path} row {line_number} has fuel inventory for a "
                "non-fuel-priced source."
            )
        try:
            order_number = int(_required(row, "dispatch_order", path, line_number))
        except ValueError as error:
            raise ValueError(
                f"{path} row {line_number} has an invalid dispatch_order."
            ) from error
        sources[source_id] = EnergySource(
            source_id=source_id,
            technology=technology,
            supply_type=supply_type,
            price_unit=price_unit,
            max_power_kw=_number(
                _required(row, "max_power_kw", path, line_number),
                "max_power_kw",
                path,
                line_number,
            ),
            dispatch_order=order_number,
            fuel_consumption_l_per_kwh=consumption,
            fuel_inventory_l=inventory,
        )
    if not sources:
        raise ValueError(f"{path} does not define any energy sources.")
    if sum(source.supply_type == "grid" for source in sources.values()) != 1:
        raise ValueError(
            "Source configuration must include exactly one ordinary grid source."
        )
    return sources


def load_hourly_availability(path, sources):
    rows = _read_csv(path, AVAILABILITY_COLUMNS)
    values = {}
    timestamps = set()
    source_timestamps = {source_id: set() for source_id in sources}
    demand_by_timestamp = {}
    for line_number, row in rows:
        timestamp_text = _required(row, "timestamp", path, line_number)
        try:
            timestamp = datetime.fromisoformat(timestamp_text)
        except ValueError as error:
            raise ValueError(
                f"{path} row {line_number} has an invalid timestamp."
            ) from error
        if timestamp.minute or timestamp.second or timestamp.microsecond:
            raise ValueError(
                f"{path} row {line_number} timestamp must be on an hour boundary."
            )
        source_id = _required(row, "source_id", path, line_number)
        if source_id not in sources:
            raise ValueError(
                f"{path} row {line_number} references unknown source {source_id!r}."
            )
        key = (timestamp, source_id)
        if key in values:
            raise ValueError(
                f"{path} contains duplicate availability for "
                f"{timestamp_text} and {source_id}."
            )
        available = _number(
            _required(row, "available_kwh", path, line_number),
            "available_kwh",
            path,
            line_number,
        )
        if available > sources[source_id].max_power_kw:
            raise ValueError(
                f"{path} row {line_number} exceeds the configured capacity "
                f"for {source_id}."
            )
        values[key] = available
        source_timestamps[source_id].add(timestamp)
        timestamps.add(timestamp)
        demand = _number(
            _required(row, "protected_it_kw", path, line_number),
            "protected_it_kw",
            path,
            line_number,
        )
        if (
            timestamp in demand_by_timestamp
            and demand_by_timestamp[timestamp] != demand
        ):
            raise ValueError(
                f"{path} has inconsistent protected demand at {timestamp_text}."
            )
        demand_by_timestamp[timestamp] = demand
    if not timestamps:
        raise ValueError(f"{path} does not contain any hourly availability.")
    ordered_timestamps = tuple(sorted(timestamps))
    if any(
        later - earlier != timedelta(hours=1)
        for earlier, later in zip(ordered_timestamps, ordered_timestamps[1:])
    ):
        raise ValueError(f"{path} timestamps must advance one hour at a time.")
    for source_id, source_hours in source_timestamps.items():
        if source_hours != timestamps:
            raise ValueError(
                f"{path} has missing or inconsistent hourly records for "
                f"{source_id}."
            )
    if len(demand_by_timestamp) != len(timestamps):
        raise ValueError(f"{path} must provide protected_it_kw for every hour.")
    availability = {
        source_id: tuple(values[(timestamp, source_id)] for timestamp in ordered_timestamps)
        for source_id in sources
    }
    demand = tuple(demand_by_timestamp[timestamp] for timestamp in ordered_timestamps)
    return ordered_timestamps, demand, availability


def load_monthly_prices(path, sources, timestamps):
    rows = _read_csv(path, PRICE_COLUMNS)
    prices = {}
    months = {timestamp.strftime("%Y-%m") for timestamp in timestamps}
    for line_number, row in rows:
        source_id = _required(row, "source_id", path, line_number)
        if source_id not in sources:
            raise ValueError(
                f"{path} row {line_number} references unknown source {source_id!r}."
            )
        month = _required(row, "month", path, line_number)
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError(
                f"{path} row {line_number} month must use YYYY-MM format."
            )
        try:
            date.fromisoformat(month + "-01")
        except ValueError as error:
            raise ValueError(f"{path} row {line_number} has invalid month.") from error
        key = (source_id, month)
        if key in prices:
            raise ValueError(
                f"{path} contains duplicate source-month record {source_id}/{month}."
            )
        unit = _required(row, "unit", path, line_number)
        if unit != sources[source_id].price_unit:
            raise ValueError(
                f"{path} row {line_number} unit {unit!r} does not match "
                f"{source_id} configuration ({sources[source_id].price_unit})."
            )
        category = _required(row, "price_category", path, line_number)
        if category not in PRICE_CATEGORIES:
            raise ValueError(
                f"{path} row {line_number} has invalid price_category {category!r}."
            )
        status = _required(row, "status", path, line_number)
        if status not in STATUSES:
            raise ValueError(f"{path} row {line_number} has invalid status {status!r}.")
        raw_price = row.get("price", "").strip()
        if status == "missing":
            if raw_price:
                raise ValueError(
                    f"{path} row {line_number}: missing prices must have a blank price."
                )
            price = None
        else:
            if not raw_price:
                raise ValueError(
                    f"{path} row {line_number} has a missing price; use status=missing "
                    "to record a data gap."
                )
            price = _number(
                raw_price,
                "price",
                path,
                line_number,
                allow_negative=unit == "RMB/kWh",
            )
        if category == "fuel_price" and unit != "RMB/litre":
            raise ValueError(
                f"{path} row {line_number}: fuel_price requires RMB/litre."
            )
        if unit == "RMB/litre" and category != "fuel_price":
            raise ValueError(
                f"{path} row {line_number}: RMB/litre must use fuel_price category."
            )
        supply_type = sources[source_id].supply_type
        if (
            category == "estimated_generation_cost_lcoe"
            and supply_type != "onsite"
        ):
            raise ValueError(
                f"{path} row {line_number}: generation cost/LCOE applies only "
                "to onsite generation."
            )
        if category == "generator_selling_price" and supply_type != "contract":
            raise ValueError(
                f"{path} row {line_number}: generator selling prices apply "
                "only to contracted electricity."
            )
        if category == "customer_purchase_price" and supply_type == "onsite":
            raise ValueError(
                f"{path} row {line_number}: onsite generation cannot use a "
                "customer purchase price."
            )
        if status in {"observed", "derived"}:
            for column in (
                "publisher",
                "source_url",
                "document_title",
                "publication_date",
                "applicable_period",
                "geography",
                "original_units",
                "method",
            ):
                _required(row, column, path, line_number)
        prices[key] = MonthlyPrice(source_id, month, price, unit, category, status)
    expected_keys = {
        (source_id, month) for source_id in sources for month in months
    }
    missing_keys = expected_keys - prices.keys()
    if missing_keys:
        source_id, month = sorted(missing_keys)[0]
        raise ValueError(
            f"{path} is missing source-month record {source_id}/{month}."
        )
    return prices


def load_emission_factors(path, sources):
    rows = _read_csv(path, EMISSION_COLUMNS)
    factors = {}
    for line_number, row in rows:
        source_id = _required(row, "source_id", path, line_number)
        if source_id not in sources:
            raise ValueError(
                f"{path} row {line_number} references unknown source {source_id!r}."
            )
        if source_id in factors:
            raise ValueError(f"{path} contains duplicate factor for {source_id}.")
        unit = _required(row, "unit", path, line_number)
        expected_unit = (
            "kgCO2/litre"
            if sources[source_id].price_unit == "RMB/litre"
            else None
        )
        if expected_unit and unit != expected_unit:
            raise ValueError(
                f"{path} row {line_number} diesel factors must use kgCO2/litre."
            )
        if not expected_unit and unit not in {
            "kgCO2/kWh",
            "kgCO2e/kWh",
        }:
            raise ValueError(
                f"{path} row {line_number} electricity factor must use "
                "kgCO2/kWh or kgCO2e/kWh."
            )
        status = _required(row, "status", path, line_number)
        if status not in STATUSES:
            raise ValueError(f"{path} row {line_number} has invalid status {status!r}.")
        raw_factor = row.get("factor", "").strip()
        if status == "missing":
            if raw_factor:
                raise ValueError(
                    f"{path} row {line_number}: missing factors must be blank."
                )
            factor = None
        else:
            if not raw_factor:
                raise ValueError(
                    f"{path} row {line_number} has a missing factor; use "
                    "status=missing to record a data gap."
                )
            factor = _number(raw_factor, "factor", path, line_number)
        boundary = _required(row, "boundary", path, line_number)
        reference_year = _required(row, "reference_year", path, line_number)
        is_lifecycle = "lifecycle" in boundary.lower()
        if (unit == "kgCO2e/kWh") != is_lifecycle:
            raise ValueError(
                f"{path} row {line_number} must keep lifecycle CO2e and direct "
                "CO2 factors in separate accounting boundaries."
            )
        if status in {"observed", "derived"}:
            for column in (
                "geography",
                "publisher",
                "source_url",
                "document_title",
                "publication_date",
                "applicable_period",
                "method",
            ):
                _required(row, column, path, line_number)
        factors[source_id] = EmissionFactor(
            source_id, factor, unit, boundary, reference_year, status
        )
    missing_sources = set(sources) - factors.keys()
    if missing_sources:
        raise ValueError(
            f"{path} is missing factors for: {', '.join(sorted(missing_sources))}."
        )
    return factors


def load_scenario_impacts(path, sources):
    """Load explicit deviations from ordinary availability and monthly prices."""
    if not Path(path).exists():
        return {}
    rows = _read_csv(path, SCENARIO_IMPACT_COLUMNS)
    impacts = {}
    for line_number, row in rows:
        scenario = _required(row, "scenario", path, line_number)
        if scenario == "ordinary":
            raise ValueError(
                f"{path} row {line_number}: ordinary operation is the implicit baseline."
            )
        source_id = _required(row, "source_id", path, line_number)
        if source_id not in sources:
            raise ValueError(
                f"{path} row {line_number} references unknown source {source_id!r}."
            )
        key = (scenario, source_id)
        if key in impacts:
            raise ValueError(
                f"{path} contains duplicate impact for {scenario}/{source_id}."
            )
        availability_multiplier = _number(
            _required(row, "availability_multiplier", path, line_number),
            "availability_multiplier",
            path,
            line_number,
        )
        if availability_multiplier > 1:
            raise ValueError(
                f"{path} row {line_number} availability_multiplier cannot exceed 1."
            )
        price_multiplier = _number(
            _required(row, "price_multiplier", path, line_number),
            "price_multiplier",
            path,
            line_number,
        )
        impacts[key] = ScenarioImpact(
            availability_multiplier, price_multiplier
        )
    return impacts


def load_energy_inputs(directory):
    directory = Path(directory)
    sources = load_sources(directory / "source_config.csv")
    timestamps, demand, availability = load_hourly_availability(
        directory / "hourly_availability.csv", sources
    )
    prices = load_monthly_prices(
        directory / "monthly_prices.csv", sources, timestamps
    )
    factors = load_emission_factors(directory / "emission_factors.csv", sources)
    scenario_impacts = load_scenario_impacts(
        directory / "scenario_impacts.csv", sources
    )
    return EnergyInputs(
        timestamps, demand, availability, sources, prices, factors, scenario_impacts
    )
