from dataclasses import dataclass
from pathlib import Path
import csv


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


# Synthetic demonstration inputs: not measured Chinese operating data.
HOURS = 24
PRICE = [
    0.30, 0.30, 0.30, 0.30, 0.30, 0.30, 0.30, 0.30,
    0.58, 0.58, 0.83, 0.83, 0.58, 0.58, 0.58, 0.83,
    0.83, 0.83, 0.83, 0.83, 0.58, 0.83, 0.58, 0.58,
]
SOLAR_KW = [
    0, 0, 0, 0, 0, 0, 0, 3,
    10, 20, 30, 38, 42, 38, 30, 20,
    10, 3, 0, 0, 0, 0, 0, 0,
]
GRID_CO2_KG_PER_KWH = [
    0.65, 0.65, 0.65, 0.65, 0.65, 0.65, 0.62, 0.60,
    0.55, 0.50, 0.45, 0.40, 0.38, 0.40, 0.45, 0.50,
    0.55, 0.60, 0.65, 0.68, 0.68, 0.68, 0.65, 0.65,
]
PROTECTED_IT_KW = [10.0] * HOURS

IT_CAPACITY_KW = 35.0
PUE = 1.2
CARBON_PRICE_RMB_PER_KG = 0.20

JOBS = [
    Job("training_A", release=0, deadline=8, compute_kwh=60),
    Job("batch_inference", release=6, deadline=17, compute_kwh=80),
    Job("training_B", release=10, deadline=24, compute_kwh=100),
]


def validate_inputs(jobs):
    arrays = [PRICE, SOLAR_KW, GRID_CO2_KG_PER_KWH, PROTECTED_IT_KW]
    if any(len(values) != HOURS for values in arrays):
        raise ValueError("Every hourly input must have HOURS entries.")

    if any(x < 0 for values in arrays[1:] for x in values):
        raise ValueError("Solar, carbon intensity, and demand must be nonnegative.")

    if PUE < 1:
        raise ValueError("PUE must be at least 1.")

    if any(x > IT_CAPACITY_KW for x in PROTECTED_IT_KW):
        raise ValueError("Protected demand exceeds computing capacity.")

    if len({job.name for job in jobs}) != len(jobs):
        raise ValueError("Job names must be unique.")

    for job in jobs:
        if not 0 <= job.release < job.deadline <= HOURS:
            raise ValueError(f"Invalid time window: {job.name}")
        if job.compute_kwh < 0:
            raise ValueError(f"Negative job energy: {job.name}")


def dispatch(schedule, battery):
    """One-hour intervals; battery starts empty and stores solar only."""
    soc = 0.0
    rows = []

    for hour in range(HOURS):
        flexible_it = sum(values[hour] for values in schedule.values())
        it_load = PROTECTED_IT_KW[hour] + flexible_it
        facility_load = PUE * it_load

        direct_solar = min(SOLAR_KW[hour], facility_load)
        solar_surplus = SOLAR_KW[hour] - direct_solar
        remaining_load = facility_load - direct_solar

        charge = min(
            solar_surplus,
            battery.power_kw,
            max(0.0, battery.capacity_kwh - soc)
            / battery.charge_efficiency,
        )
        soc += charge * battery.charge_efficiency

        discharge = min(
            remaining_load,
            battery.power_kw,
            soc * battery.discharge_efficiency,
        )
        soc -= discharge / battery.discharge_efficiency
        soc = max(0.0, soc)

        grid = remaining_load - discharge

        rows.append({
            "hour": hour,
            "protected_it_kwh": PROTECTED_IT_KW[hour],
            "flexible_it_kwh": flexible_it,
            "facility_kwh": facility_load,
            "solar_available_kwh": SOLAR_KW[hour],
            "solar_direct_kwh": direct_solar,
            "battery_charge_kwh": charge,
            "battery_discharge_kwh": discharge,
            "battery_soc_kwh": soc,
            "solar_curtailed_kwh": solar_surplus - charge,
            "grid_kwh": grid,
            "electricity_cost_rmb": grid * PRICE[hour],
            "grid_emissions_kg": grid * GRID_CO2_KG_PER_KWH[hour],
        })

    return rows


def objective(schedule, battery, carbon_price):
    rows = dispatch(schedule, battery)
    return sum(
        row["electricity_cost_rmb"]
        + carbon_price * row["grid_emissions_kg"]
        for row in rows
    )


def build_schedule(jobs, battery, smart=False, quantum_kwh=1.0,
                   carbon_price=CARBON_PRICE_RMB_PER_KG):
    validate_inputs(jobs)

    if quantum_kwh <= 0:
        raise ValueError("Scheduling quantum must be positive.")

    schedule = {job.name: [0.0] * HOURS for job in jobs}
    spare = [IT_CAPACITY_KW - load for load in PROTECTED_IT_KW]

    # Earliest-deadline-first admission; greedy placement within each job.
    for job in sorted(jobs, key=lambda item: (item.deadline, item.release)):
        remaining = job.compute_kwh

        while remaining > 1e-9:
            candidates = [
                hour for hour in range(job.release, job.deadline)
                if spare[hour] > 1e-9
            ]

            if not candidates:
                raise ValueError(
                    f"Could not place {job.name}. This greedy heuristic may "
                    "fail even when a different joint schedule is feasible."
                )

            if smart:
                scores = {}
                for hour in candidates:
                    amount = min(quantum_kwh, remaining, spare[hour])
                    schedule[job.name][hour] += amount
                    scores[hour] = objective(schedule, battery, carbon_price)
                    schedule[job.name][hour] -= amount

                chosen = min(candidates, key=lambda hour: (scores[hour], hour))
            else:
                chosen = min(candidates)

            amount = min(quantum_kwh, remaining, spare[chosen])
            schedule[job.name][chosen] += amount
            spare[chosen] -= amount
            remaining -= amount

    return schedule


def verify_schedule(jobs, schedule):
    for job in jobs:
        values = schedule[job.name]
        assert abs(sum(values) - job.compute_kwh) < 1e-6
        assert all(
            abs(value) < 1e-9
            for hour, value in enumerate(values)
            if not job.release <= hour < job.deadline
        )

    for hour in range(HOURS):
        load = PROTECTED_IT_KW[hour] + sum(
            values[hour] for values in schedule.values()
        )
        assert load <= IT_CAPACITY_KW + 1e-6


def summarize(name, rows):
    return {
        "strategy": name,
        "cost_rmb": sum(row["electricity_cost_rmb"] for row in rows),
        "grid_emissions_kg": sum(row["grid_emissions_kg"] for row in rows),
        "grid_energy_kwh": sum(row["grid_kwh"] for row in rows),
        "peak_grid_kw": max(row["grid_kwh"] for row in rows),
        "solar_curtailed_kwh": sum(
            row["solar_curtailed_kwh"] for row in rows
        ),
        "ending_battery_kwh": rows[-1]["battery_soc_kwh"],
    }


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    battery = Battery()
    output = Path("results")
    output.mkdir(exist_ok=True)
    summaries = []

    strategies = [
        ("immediate", False, 0.0),
        ("price_aware", True, 0.0),
        ("price_carbon_aware", True, CARBON_PRICE_RMB_PER_KG),
    ]

    for name, smart, carbon_price in strategies:
        schedule = build_schedule(
            JOBS, battery, smart=smart, carbon_price=carbon_price
        )
        verify_schedule(JOBS, schedule)
        rows = dispatch(schedule, battery)

        for row in rows:
            for job in JOBS:
                row[f"job_{job.name}_kwh"] = schedule[job.name][row["hour"]]

        summaries.append(summarize(name, rows))
        write_csv(output / f"{name}_hourly.csv", rows)

    write_csv(output / "comparison.csv", summaries)

    for result in summaries:
        print(
            f"{result['strategy']:20s} "
            f"cost={result['cost_rmb']:.2f} RMB | "
            f"grid emissions={result['grid_emissions_kg']:.2f} kg | "
            f"peak grid={result['peak_grid_kw']:.2f} kW"
        )


if __name__ == "__main__":
    main()
