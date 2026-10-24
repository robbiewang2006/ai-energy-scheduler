#!/usr/bin/env python3
"""baseline.py - No-scheduling (ASAP, back-to-back) baseline.

This is an *independent* program: it reads the CSVs directly and does not
import the main simulator. It simulates one year (default 8760 hourly slots) under
the simplest possible policy:

    Every time a task arrives (at its `start_time`), place it at the first
    hour at which no task is running -- i.e. a single-server FIFO queue that
    runs each task to completion before the next begins.

Each task occupies `duration` consecutive hours and draws
`energy_needed / duration` kW in each of those hours (the facility load is
scaled by PUE, matching the main simulator). All energy is charged to a single
supply source (default the grid). The printed objective is

    objective = total_cost + carbon_price * total_emissions

Usage:
    python baseline.py
    python baseline.py --source wind
    python baseline.py --source ordinary_grid --pue 1.2 --carbon-price 0.2
"""
import argparse
import csv
from pathlib import Path

DEFAULT_HORIZON = 8760
DEFAULT_PUE = 1.2
DEFAULT_CARBON_PRICE = 0.20
DEFAULT_SOURCE = "ordinary_grid"
GRID_PROXY = "natural_gas_fired"  # dispatchable, grid-like fallback


def load_tasks(path):
    """Read tasks.csv -> list of (task_id, start_time, duration, energy_needed)."""
    tasks = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            task_id = (row.get("task_id") or "").strip()
            if not task_id:
                continue
            start = int(float(row["start_time"]))
            duration = int(float(row["duration"]))
            energy = float(row["energy_needed"])
            if duration <= 0:
                # Degenerate duration: collapse to one hour.
                duration = 1
            tasks.append((task_id, start, duration, energy))
    # Arrival order = start_time (the order tasks are "received").
    tasks.sort(key=lambda t: t[1])
    return tasks


def available_sources(prices_path):
    with open(prices_path, newline="", encoding="utf-8-sig") as f:
        return {row["source_id"] for row in csv.DictReader(f)}


def load_monthly_prices(path, source):
    prices = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["source_id"] == source:
                prices[row["month"].strip()] = float(row["price"])
    return prices


def load_emission_factor(path, source):
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["source_id"] == source:
                return float(row["factor"])
    return None


def month_label_for_hour(hour, horizon, months=12):
    """Map an hourly index to a 2-digit month label (e.g. 01..12)."""
    idx = min(hour * months // horizon, months - 1)
    return f"{idx + 1:02d}"


def simulate(tasks, prices, factor, horizon, pue, carbon_price):
    """Run the FIFO ASAP queue and accumulate cost + emissions."""
    busy_until = 0
    total_cost = 0.0
    total_emissions = 0.0
    scheduled = 0
    overflow = 0
    if not prices:
        raise ValueError("No monthly prices available for the chosen source.")

    for _task_id, start, duration, energy in tasks:
        begin = max(start, busy_until)
        if begin >= horizon:
            overflow += 1
            continue
        per_hour = energy / duration
        end = min(begin + duration, horizon)
        for h in range(begin, end):
            month = month_label_for_hour(h, horizon)
            price = prices.get(month, next(iter(prices.values())))
            facility_energy = per_hour * pue
            total_cost += facility_energy * price
            total_emissions += facility_energy * factor
        busy_until = begin + duration
        scheduled += 1

    objective = total_cost + carbon_price * total_emissions
    return {
        "scheduled": scheduled,
        "overflow": overflow,
        "total_cost": total_cost,
        "total_emissions": total_emissions,
        "objective": objective,
    }


def main():
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="No-scheduling ASAP baseline.")
    ap.add_argument("--tasks", default=str(here / "data" / "tasks.csv"))
    ap.add_argument("--prices", default=str(here / "data" / "monthly_prices.csv"))
    ap.add_argument("--emissions", default=str(here / "data" / "emission_factors.csv"))
    ap.add_argument("--source", default=DEFAULT_SOURCE,
                    help="Supply source whose price/EF to charge against.")
    ap.add_argument("--horizon", type=int, default=DEFAULT_HORIZON)
    ap.add_argument("--pue", type=float, default=DEFAULT_PUE)
    ap.add_argument("--carbon-price", type=float, default=DEFAULT_CARBON_PRICE)
    args = ap.parse_args()

    tasks = load_tasks(args.tasks)

    source = args.source
    available = available_sources(args.prices)
    if source not in available:
        proxy = GRID_PROXY if GRID_PROXY in available else next(iter(available))
        print(f"[warn] source {source!r} not found in prices; "
              f"using {proxy!r} as a grid-like proxy.")
        source = proxy

    prices = load_monthly_prices(args.prices, source)
    factor = load_emission_factor(args.emissions, source)
    if factor is None:
        raise SystemExit(f"emission factor for source {source!r} not found.")

    res = simulate(tasks, prices, factor, args.horizon, args.pue, args.carbon_price)

    print("Baseline (no scheduling: ASAP, back-to-back FIFO queue)")
    print(f"  source             : {source}")
    print(f"  tasks total        : {len(tasks)}")
    print(f"  tasks scheduled    : {res['scheduled']}")
    print(f"  tasks overflow year: {res['overflow']}")
    print(f"  total cost (RMB)   : {res['total_cost']:.2f}")
    print(f"  total emissions kg : {res['total_emissions']:.2f}")
    print(f"  objective (cost+{args.carbon_price}*emissions): "
          f"{res['objective']:.2f} RMB")


if __name__ == "__main__":
    main()
