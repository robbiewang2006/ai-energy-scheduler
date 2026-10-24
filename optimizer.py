"""Projected finite-difference descent evaluated with the original dispatch policy."""

import math

import numpy as np
from scipy.optimize import linprog, minimize

from scheduler import (
    ANNUAL_DEMAND_REFERENCE_KWH, CARBON_PRICE_RMB_PER_KG, IT_CAPACITY_KW, PUE,
    SYNTHETIC_INPUTS, InitializationFailure, SupplyShortage, build_schedule, constraint_violation,
    dispatch, summarize, validate_inputs,
)


def finite_difference_gradient(evaluate, point, step=1e-5):
    """Central differences; one-sided at dispatch feasibility boundaries.

    Probes need not complete jobs or respect computing capacity: dispatch is
    still the exact supply/cost model there. Negative allocations and supply
    shortages are excluded. If neither side is evaluable, that partial is zero;
    this can stall at a boundary and is not a stationarity certificate.
    """
    base = evaluate(point)
    gradient = np.zeros_like(point)
    for index in range(len(point)):
        plus, minus = point.copy(), point.copy()
        plus[index] += step
        minus[index] -= step
        upper = evaluate(plus)
        lower = evaluate(minus) if minus[index] >= 0 else None
        if upper is not None and lower is not None:
            gradient[index] = (upper - lower) / (2 * step)
        elif upper is not None:
            gradient[index] = (upper - base) / step
        elif lower is not None:
            gradient[index] = (base - lower) / step
    return gradient


def optimize_schedule(
    jobs, battery, inputs=SYNTHETIC_INPUTS, scenario="ordinary",
    carbon_weight=CARBON_PRICE_RMB_PER_KG, learning_rate=1.0,
    max_iterations=100, tolerance=1e-7, initialization="uniform", seed=0,
    finite_difference_step=1e-5,
    annual_demand_reference_kwh=ANNUAL_DEMAND_REFERENCE_KWH,
):
    """Return (best feasible schedule, history), without claiming global optimality.

    SLSQP solves only the convex Euclidean projection, not the dispatch problem.
    HiGHS first checks joint computing feasibility. Backtracking rejects original
    dispatch shortages, preserving fixed source priorities and battery policy.
    """
    validate_inputs(jobs, inputs)
    for name, value in (
        ("learning_rate", learning_rate), ("tolerance", tolerance),
        ("finite_difference_step", finite_difference_step),
        ("annual_demand_reference_kwh", annual_demand_reference_kwh),
    ):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive.")
    if not math.isfinite(carbon_weight) or carbon_weight < 0:
        raise ValueError("carbon_weight must be finite and nonnegative.")
    if not isinstance(max_iterations, int) or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer.")
    if initialization not in {"uniform", "random", "immediate", "greedy"}:
        raise ValueError("Unknown initialization method.")

    hours = len(inputs.timestamps)
    active_jobs = [job for job in jobs if job.compute_kwh > 0]
    cells = [
        (job.name, hour) for job in active_jobs
        for hour in range(job.release, job.deadline)
    ]
    spare = np.array([IT_CAPACITY_KW - load for load in inputs.protected_it_kw])
    allocation_scale = max(1.0, float(max(spare)))
    facility_energy = PUE * (
        sum(inputs.protected_it_kw) + sum(job.compute_kwh for job in jobs)
    )
    score_scale = max(1.0, facility_energy)
    energy = np.array([job.compute_kwh for job in active_jobs]) / allocation_scale
    equality = np.array([
        [float(name == job.name) for name, _ in cells] for job in active_jobs
    ])
    capacity = np.array([
        [float(hour == time) for _, time in cells] for hour in range(hours)
    ])
    limits = spare / allocation_scale

    def unpack(point):
        schedule = {job.name: [0.0] * hours for job in jobs}
        for value, (name, hour) in zip(point, cells):
            schedule[name][hour] = float(value * allocation_scale)
        return schedule

    def evaluate(point):
        try:
            rows = dispatch(unpack(point), battery, inputs, scenario)
        except SupplyShortage:
            return None
        return sum(
            row["electricity_cost_rmb"] + carbon_weight * row["emissions_kg"]
            for row in rows
        ) / score_scale

    if cells:
        feasible = linprog(
            np.zeros(len(cells)), A_ub=capacity, b_ub=limits,
            A_eq=equality, b_eq=energy, bounds=(0, None), method="highs",
        )
        if not feasible.success:
            if feasible.status == 2:
                raise ValueError("Infeasible job energies/windows and shared IT capacity.")
            raise ValueError(f"Computing feasibility solver failed: {feasible.message}")
        constraints = [
            {"type": "eq", "fun": lambda x: equality @ x - energy,
             "jac": lambda x: equality},
            {"type": "ineq", "fun": lambda x: limits - capacity @ x,
             "jac": lambda x: -capacity},
        ]

        def project(target):
            result = minimize(
                lambda x: 0.5 * np.sum((x - target) ** 2), feasible.x,
                jac=lambda x: x - target, method="SLSQP",
                bounds=[(0, None)] * len(cells), constraints=constraints,
                options={"ftol": 1e-12, "maxiter": 500},
            )
            residual = max(
                float(np.max(np.abs(equality @ result.x - energy))),
                float(np.max(capacity @ result.x - limits)),
                float(np.max(-result.x)),
            ) * allocation_scale
            if not result.success or residual > 1e-7:
                raise ValueError(f"Joint projection failed: {result.message}")
            return result.x

        if initialization in {"immediate", "greedy"}:
            initial = build_schedule(
                jobs, battery, smart=initialization == "greedy",
                carbon_price=carbon_weight, inputs=inputs, scenario=scenario,
            )
            point = project(np.array([
                initial[name][hour] / allocation_scale for name, hour in cells
            ]))
        else:
            # Uniform per job's actual window, independent of its release time.
            by_name = {job.name: job for job in active_jobs}
            target = np.array([
                by_name[name].compute_kwh
                / (by_name[name].deadline - by_name[name].release)
                for name, _ in cells
            ]) / allocation_scale
            if initialization == "random":
                target *= np.random.default_rng(seed).uniform(0, 2, len(cells))
            point = project(target)
    else:
        point = np.array([])

    value = evaluate(point)
    if value is None:
        raise InitializationFailure(
            "Cannot initialize a supply-feasible schedule under original dispatch. "
            "Try another initialization; this is not proof of global infeasibility."
        )
    best, best_value = point.copy(), value
    history = []

    def record(iteration, status, rejected):
        schedule = unpack(point)
        rows = dispatch(schedule, battery, inputs, scenario)
        violation = constraint_violation(jobs, schedule, rows, battery, inputs)
        if violation > 1e-6:
            raise ValueError(f"Candidate constraint violation: {violation} kWh.")
        metrics = summarize(
            "gradient", rows, carbon_weight, annual_demand_reference_kwh
        )
        history.append({
            "iteration": iteration, "cost_rmb": metrics["cost_rmb"],
            "emissions_kg": metrics["emissions_kg"], "score_rmb": metrics["score_rmb"],
            "max_constraint_violation_kwh": violation, "feasible": True,
            "status": status, "rejected_supply_trials": rejected,
        })

    record(0, "initialized", 0)
    for iteration in range(1, max_iterations + 1):
        if not cells:
            history[-1]["status"] = "no_flexible_energy"
            break
        gradient = finite_difference_gradient(evaluate, point, finite_difference_step)
        step = learning_rate
        accepted = False
        rejected = 0
        for _ in range(30):
            candidate = project(point - step * gradient)
            candidate_value = evaluate(candidate)
            if candidate_value is None:
                rejected += 1
            elif candidate_value <= value:
                accepted = True
                break
            step *= 0.5
        if not accepted:
            record(iteration, "line_search_stalled", rejected)
            break
        movement = float(np.max(np.abs(candidate - point)))
        improvement = value - candidate_value
        point, value = candidate, candidate_value
        if value < best_value:
            best, best_value = point.copy(), value
        converged = movement <= tolerance or improvement <= tolerance
        record(iteration, "converged" if converged else "running", rejected)
        if converged:
            break
    else:
        history[-1]["status"] = "max_iterations"
    return unpack(best), history
