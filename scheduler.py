from dataclasses import dataclass
from pathlib import Path
import csv
import math

from energy_data import (
    fuel_litres_to_electricity_kwh,
    fuel_price_rmb_per_kwh,
    load_energy_inputs,
)


@dataclass(frozen=True)
class Job:
    name: str
    release: int
    deadline: int
    compute_kwh: float


@dataclass(frozen=True)
class Battery:
    capacity_kwh: float = 30.0
    power_kw: float = 10.0
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95


IT_CAPACITY_KW = 35.0
PUE = 1.2
CARBON_PRICE_RMB_PER_KG = 0.20
ANNUAL_DEMAND_REFERENCE_KWH = 1_000_000_000.0


class SupplyShortage(ValueError):
    """The unchanged dispatch policy cannot supply a candidate schedule."""


class PlacementFailure(ValueError):
    """A baseline heuristic could not place all requested job energy."""


SYNTHETIC_INPUTS = load_energy_inputs(
    Path(__file__).resolve().parent / "examples" / "synthetic"
)

JOBS = [
    Job("training_A", release=0, deadline=8, compute_kwh=60),
    Job("batch_inference", release=6, deadline=17, compute_kwh=80),
    Job("training_B", release=10, deadline=24, compute_kwh=100),
]


def validate_inputs(jobs, inputs=SYNTHETIC_INPUTS):
    if PUE < 1:
        raise ValueError("PUE must be at least 1.")

    if any(x > IT_CAPACITY_KW for x in inputs.protected_it_kw):
        raise ValueError("Protected demand exceeds computing capacity.")

    if len({job.name for job in jobs}) != len(jobs):
        raise ValueError("Job names must be unique.")

    for job in jobs:
        if not 0 <= job.release < job.deadline <= len(inputs.timestamps):
            raise ValueError(f"Invalid time window: {job.name}")
        if not math.isfinite(job.compute_kwh) or job.compute_kwh < 0:
            raise ValueError(f"Invalid job energy: {job.name}")


def _scenario_impact(source_id, scenario, inputs):
    return inputs.scenario_impacts.get((scenario, source_id))


def _scenario_availability(source_id, hour, scenario, inputs):
    # A source without a scenario row retains its ordinary hourly availability.
    impact = _scenario_impact(source_id, scenario, inputs)
    multiplier = impact.availability_multiplier if impact else 1.0
    return inputs.availability_kwh[source_id][hour] * multiplier


def _source_accounting(source_id, amount_kwh, timestamp, inputs, scenario):
    if amount_kwh <= 1e-12:
        return 0.0, 0.0
    source = inputs.sources[source_id]
    month = timestamp.strftime("%Y-%m")
    price_record = inputs.monthly_prices[(source_id, month)]
    if price_record.price is None:
        raise ValueError(
            f"Missing {source_id} price for {month}; refusing to use zero."
        )
    factor_record = inputs.emission_factors[source_id]
    if factor_record.factor is None:
        raise ValueError(
            f"Missing emissions factor for used source {source_id}; "
            "refusing to use zero."
        )
    if source.price_unit == "RMB/litre":
        fuel_litres = amount_kwh * source.fuel_consumption_l_per_kwh
        impact = _scenario_impact(source_id, scenario, inputs)
        price_multiplier = impact.price_multiplier if impact else 1.0
        return (
            fuel_litres * price_record.price * price_multiplier,
            fuel_litres * factor_record.factor,
        )
    impact = _scenario_impact(source_id, scenario, inputs)
    price_multiplier = impact.price_multiplier if impact else 1.0
    return (
        amount_kwh * price_record.price * price_multiplier,
        amount_kwh * factor_record.factor,
    )


def dispatch(schedule, battery, inputs=SYNTHETIC_INPUTS, scenario="ordinary"):
    """Dispatch hourly supply; battery starts empty and stores onsite solar only."""
    validate_inputs([], inputs)
    if (
        not all(math.isfinite(value) for value in (
            battery.capacity_kwh, battery.power_kw,
            battery.charge_efficiency, battery.discharge_efficiency,
        ))
        or battery.capacity_kwh < 0 or battery.power_kw < 0
        or not 0 < battery.charge_efficiency <= 1
        or not 0 < battery.discharge_efficiency <= 1
    ):
        raise ValueError("Invalid battery capacity, power, or efficiency.")
    if any(
        len(values) != len(inputs.timestamps)
        or any(not math.isfinite(value) or value < -1e-9 for value in values)
        for values in schedule.values()
    ):
        raise ValueError("Schedule must contain finite nonnegative hourly values.")
    if scenario != "ordinary" and not any(
        impact_scenario == scenario
        for impact_scenario, _ in inputs.scenario_impacts
    ):
        raise ValueError(f"Unknown energy scenario: {scenario}.")
    soc = 0.0
    rows = []
    fuel_used_l = {source_id: 0.0 for source_id in inputs.sources}
    accounting_classes = set()

    for hour, timestamp in enumerate(inputs.timestamps):
        flexible_it = sum(values[hour] for values in schedule.values())
        it_load = inputs.protected_it_kw[hour] + flexible_it
        facility_load = PUE * it_load

        solar_sources = sorted(
            (
                source for source in inputs.sources.values()
                if source.technology == "solar_pv"
                and source.supply_type == "onsite"
            ),
            key=lambda source: (source.dispatch_order, source.source_id),
        )
        used_by_source = {source_id: 0.0 for source_id in inputs.sources}
        solar_surplus_by_source = {}
        remaining_load = facility_load
        for source in solar_sources:
            available = _scenario_availability(
                source.source_id, hour, scenario, inputs
            )
            direct = min(available, remaining_load)
            used_by_source[source.source_id] += direct
            solar_surplus_by_source[source.source_id] = available - direct
            remaining_load -= direct
        solar_surplus = sum(solar_surplus_by_source.values())
        charge = min(
            solar_surplus,
            battery.power_kw,
            max(0.0, battery.capacity_kwh - soc)
            / battery.charge_efficiency,
        )
        charge_left = charge
        for source in solar_sources:
            source_charge = min(
                solar_surplus_by_source.get(source.source_id, 0.0),
                charge_left,
            )
            used_by_source[source.source_id] += source_charge
            charge_left -= source_charge
        soc += charge * battery.charge_efficiency

        discharge = min(
            remaining_load,
            battery.power_kw,
            soc * battery.discharge_efficiency,
        )
        soc -= discharge / battery.discharge_efficiency
        soc = max(0.0, soc)

        remaining_load -= discharge
        other_sources = sorted(
            (
                source for source in inputs.sources.values()
                if source not in solar_sources
            ),
            key=lambda source: (source.dispatch_order, source.source_id),
        )
        # Dispatch order can reserve emergency generators for actual shortfalls.
        for source in other_sources:
            available = _scenario_availability(
                source.source_id, hour, scenario, inputs
            )
            limit = min(available, source.max_power_kw)
            if source.fuel_consumption_l_per_kwh is not None:
                limit = min(
                    limit,
                    fuel_litres_to_electricity_kwh(
                        source.fuel_inventory_l - fuel_used_l[source.source_id],
                        source.fuel_consumption_l_per_kwh,
                    ),
                )
            amount = min(max(0.0, limit), remaining_load)
            used_by_source[source.source_id] += amount
            remaining_load -= amount
            if source.fuel_consumption_l_per_kwh is not None:
                fuel_used_l[source.source_id] += (
                    amount * source.fuel_consumption_l_per_kwh
                )

        if remaining_load > 1e-6:
            raise SupplyShortage(
                f"Available supplies cannot meet the facility load at {timestamp}."
            )

        source_costs = {}
        source_emissions = {}
        for source_id, amount in used_by_source.items():
            cost, emissions = _source_accounting(
                source_id, amount, timestamp, inputs, scenario
            )
            source_costs[source_id] = cost
            source_emissions[source_id] = emissions
            if amount > 1e-12:
                factor = inputs.emission_factors[source_id]
                accounting_classes.add((
                    "CO2e" if factor.unit.startswith("kgCO2e") else "CO2",
                    "lifecycle"
                    if "lifecycle" in factor.boundary.lower()
                    else "direct",
                ))
        grid = sum(
            used_by_source[source_id]
            for source_id, source in inputs.sources.items()
            if source.supply_type == "grid"
        )
        grid_emissions = sum(
            source_emissions[source_id]
            for source_id, source in inputs.sources.items()
            if source.supply_type == "grid"
        )

        row = {
            "hour": hour,
            "timestamp": timestamp.isoformat(),
            "scenario": scenario,
            "protected_it_kwh": inputs.protected_it_kw[hour],
            "flexible_it_kwh": flexible_it,
            "facility_kwh": facility_load,
            "solar_available_kwh": sum(
                _scenario_availability(source.source_id, hour, scenario, inputs)
                for source in solar_sources
            ),
            "solar_direct_kwh": sum(
                used_by_source[source.source_id] for source in solar_sources
            ) - charge,
            "battery_charge_kwh": charge,
            "battery_discharge_kwh": discharge,
            "battery_soc_kwh": soc,
            "solar_curtailed_kwh": solar_surplus - charge,
            "grid_kwh": grid,
            "grid_emissions_kg": grid_emissions,
            "electricity_cost_rmb": sum(source_costs.values()),
            "emissions_kg": sum(source_emissions.values()),
        }
        for source_id, amount in used_by_source.items():
            source = inputs.sources[source_id]
            row[f"source_{source_id}_kwh"] = amount
            row[f"source_{source_id}_cost_rmb"] = source_costs[source_id]
            row[f"source_{source_id}_emissions_kg"] = source_emissions[source_id]
            monthly_price = inputs.monthly_prices[
                (source_id, timestamp.strftime("%Y-%m"))
            ]
            impact = _scenario_impact(source_id, scenario, inputs)
            price_multiplier = impact.price_multiplier if impact else 1.0
            factor = inputs.emission_factors[source_id]
            effective_price = monthly_price.price
            if effective_price is not None:
                if source.price_unit == "RMB/litre":
                    effective_price = fuel_price_rmb_per_kwh(
                        effective_price, source.fuel_consumption_l_per_kwh
                    )
                effective_price *= price_multiplier
            row[f"source_{source_id}_effective_price_rmb_per_kwh"] = (
                effective_price
            )
            row[f"source_{source_id}_price_category"] = (
                monthly_price.price_category
            )
            row[f"source_{source_id}_price_unit"] = monthly_price.unit
            row[f"source_{source_id}_price_status"] = monthly_price.status
            row[f"source_{source_id}_price_scenario_multiplier"] = price_multiplier
            row[f"source_{source_id}_emission_unit"] = factor.unit
            row[f"source_{source_id}_emission_boundary"] = factor.boundary
        rows.append(row)

    if len(accounting_classes) > 1:
        raise ValueError(
            "Cannot combine lifecycle and direct emissions factors in "
            "one objective; configure a consistent accounting boundary."
        )
    return rows


def objective(
    schedule, battery, carbon_price, inputs=SYNTHETIC_INPUTS, scenario="ordinary"
):
    rows = dispatch(schedule, battery, inputs, scenario)
    return sum(
        row["electricity_cost_rmb"]
        + carbon_price * row["emissions_kg"]
        for row in rows
    )


def build_schedule(jobs, battery, smart=False, quantum_kwh=1.0,
                   carbon_price=CARBON_PRICE_RMB_PER_KG,
                   inputs=SYNTHETIC_INPUTS, scenario="ordinary"):
    validate_inputs(jobs, inputs)

    if quantum_kwh <= 0:
        raise ValueError("Scheduling quantum must be positive.")

    schedule = {job.name: [0.0] * len(inputs.timestamps) for job in jobs}
    spare = [IT_CAPACITY_KW - load for load in inputs.protected_it_kw]

    # Earliest-deadline-first admission; greedy placement within each job.
    for job in sorted(jobs, key=lambda item: (item.deadline, item.release)):
        remaining = job.compute_kwh

        while remaining > 1e-9:
            candidates = [
                hour for hour in range(job.release, job.deadline)
                if spare[hour] > 1e-9
            ]

            if not candidates:
                raise PlacementFailure(
                    f"Could not place {job.name}. This greedy heuristic may "
                    "fail even when a different joint schedule is feasible."
                )

            if smart:
                scores = {}
                for hour in candidates:
                    amount = min(quantum_kwh, remaining, spare[hour])
                    schedule[job.name][hour] += amount
                    try:
                        scores[hour] = objective(
                            schedule, battery, carbon_price, inputs, scenario
                        )
                    except SupplyShortage:
                        continue
                    finally:
                        schedule[job.name][hour] -= amount

                if not scores:
                    raise PlacementFailure(
                        f"No supply-feasible greedy placement for {job.name}; "
                        "this heuristic failure is not proof of infeasibility."
                    )
                chosen = min(scores, key=lambda hour: (scores[hour], hour))
            else:
                chosen = min(candidates)

            amount = min(quantum_kwh, remaining, spare[chosen])
            schedule[job.name][chosen] += amount
            spare[chosen] -= amount
            remaining -= amount

    return schedule


def verify_schedule(jobs, schedule, inputs=SYNTHETIC_INPUTS):
    for job in jobs:
        values = schedule[job.name]
        assert len(values) == len(inputs.timestamps)
        assert all(math.isfinite(value) and value >= -1e-9 for value in values)
        assert abs(sum(values) - job.compute_kwh) < 1e-6
        assert all(
            abs(value) < 1e-9
            for hour, value in enumerate(values)
            if not job.release <= hour < job.deadline
        )

    for hour in range(len(inputs.timestamps)):
        load = inputs.protected_it_kw[hour] + sum(
            values[hour] for values in schedule.values()
        )
        assert load <= IT_CAPACITY_KW + 1e-6


def constraint_violation(jobs, schedule, rows, battery, inputs=SYNTHETIC_INPUTS):
    """Maximum absolute energy residual or positive bound violation, in kWh."""
    violations = [0.0]
    for job in jobs:
        values = schedule[job.name]
        violations.append(abs(sum(values) - job.compute_kwh))
        violations.extend(max(0.0, -value) for value in values)
        violations.extend(
            abs(value) for hour, value in enumerate(values)
            if not job.release <= hour < job.deadline
        )
    soc = 0.0
    for hour, row in enumerate(rows):
        it = inputs.protected_it_kw[hour] + sum(
            values[hour] for values in schedule.values()
        )
        charge = row["battery_charge_kwh"]
        discharge = row["battery_discharge_kwh"]
        supply = sum(row[f"source_{source_id}_kwh"] for source_id in inputs.sources)
        violations.extend((
            max(0.0, it - IT_CAPACITY_KW),
            abs(row["protected_it_kwh"] - inputs.protected_it_kw[hour]),
            abs(row["facility_kwh"] - PUE * it),
            abs(supply + discharge - charge - row["facility_kwh"]),
            abs(row["battery_soc_kwh"] - (
                soc + charge * battery.charge_efficiency
                - discharge / battery.discharge_efficiency
            )),
            max(0.0, -row["battery_soc_kwh"]),
            max(0.0, row["battery_soc_kwh"] - battery.capacity_kwh),
            max(0.0, -charge, -discharge),
            max(0.0, charge - battery.power_kw, discharge - battery.power_kw),
        ))
        soc = row["battery_soc_kwh"]
    return max(violations)


def summarize(name, rows, carbon_weight=CARBON_PRICE_RMB_PER_KG,
              annual_demand_reference_kwh=ANNUAL_DEMAND_REFERENCE_KWH):
    if not math.isfinite(carbon_weight) or carbon_weight < 0:
        raise ValueError("Carbon weight must be finite and nonnegative.")
    if (not math.isfinite(annual_demand_reference_kwh)
            or annual_demand_reference_kwh <= 0):
        raise ValueError("Annual demand reference must be finite and positive.")
    facility_energy = sum(row["facility_kwh"] for row in rows)
    if facility_energy <= 0:
        raise ValueError("Cannot normalize zero facility energy.")
    cost = sum(row["electricity_cost_rmb"] for row in rows)
    emissions = sum(row["emissions_kg"] for row in rows)
    factor = annual_demand_reference_kwh / facility_energy
    result = {
        "strategy": name,
        "cost_rmb": cost,
        "emissions_kg": emissions,
        "carbon_weight_rmb_per_kg": carbon_weight,
        "score_rmb": cost + carbon_weight * emissions,
        "facility_energy_kwh": facility_energy,
        "annual_demand_reference_kwh": annual_demand_reference_kwh,
        "normalization_factor": factor,
        "cost_per_1twh_rmb": cost * 1e9 / facility_energy,
        "emissions_per_1twh_kg": emissions * 1e9 / facility_energy,
        "score_per_1twh_rmb": (cost + carbon_weight * emissions) * 1e9 / facility_energy,
        "cost_per_reference_rmb": cost * factor,
        "emissions_per_reference_kg": emissions * factor,
        "score_per_reference_rmb": (cost + carbon_weight * emissions) * factor,
        "simulation_start": rows[0]["timestamp"],
        "simulation_end": rows[-1]["timestamp"],
        "simulation_hours": len(rows),
        "metric_scope": "normalized scenario, not an annual forecast",
        "grid_energy_kwh": sum(row["grid_kwh"] for row in rows),
        "peak_grid_kw": max(row["grid_kwh"] for row in rows),
        "solar_curtailed_kwh": sum(
            row["solar_curtailed_kwh"] for row in rows
        ),
        "ending_battery_kwh": rows[-1]["battery_soc_kwh"],
    }
    return result


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file, fieldnames=list(dict.fromkeys(key for row in rows for key in row))
        )
        writer.writeheader()
        writer.writerows(rows)


def main(argv=None):
    from mother import main as mother_main

    mother_main(argv)


if __name__ == "__main__":
    main()
