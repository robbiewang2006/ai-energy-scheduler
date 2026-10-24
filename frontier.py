from dataclasses import dataclass, replace


@dataclass(frozen=True)
class ProcurementOption:
    source_id: str
    technology: str
    supply_type: str
    price_category: str
    availability_kwh: float
    price_rmb_per_kwh: float
    emissions_kg_per_kwh: float


def _procurement_options(inputs):
    month_hours = {}
    for timestamp in inputs.timestamps:
        month = timestamp.strftime("%Y-%m")
        month_hours[month] = month_hours.get(month, 0) + 1
    total_hours = len(inputs.timestamps)
    options = []

    for source_id, source in inputs.sources.items():
        prices = [
            inputs.monthly_prices[(source_id, month)]
            for month in month_hours
        ]
        factor = inputs.emission_factors[source_id]
        if (
            any(record.price is None for record in prices)
            or factor.factor is None
        ):
            continue

        average_price = sum(
            record.price * month_hours[record.month]
            for record in prices
        ) / total_hours
        if source.price_unit == "RMB/litre":
            average_price *= source.fuel_consumption_l_per_kwh
            emissions = (
                factor.factor * source.fuel_consumption_l_per_kwh
            )
        else:
            emissions = factor.factor

        availability = sum(inputs.availability_kwh[source_id])
        if source.fuel_inventory_l is not None:
            availability = min(
                availability,
                source.fuel_inventory_l / source.fuel_consumption_l_per_kwh,
            )
        if availability > 0:
            options.append(ProcurementOption(
                source_id=source_id,
                technology=source.technology,
                supply_type=source.supply_type,
                price_category=", ".join(sorted({
                    record.price_category for record in prices
                })),
                availability_kwh=availability,
                price_rmb_per_kwh=average_price,
                emissions_kg_per_kwh=emissions,
            ))

    if not options:
        raise ValueError("No sources have both complete prices and emissions data.")
    emission_units = {
        (
            "kgCO2e/kWh"
            if inputs.emission_factors[option.source_id].unit.startswith("kgCO2e")
            else "kgCO2/kWh"
        )
        for option in options
    }
    if len(emission_units) != 1:
        raise ValueError(
            "Cannot compare direct CO2 and lifecycle CO2e factors in one plot."
        )
    return options, next(iter(emission_units))


def _allocate(options, target_kwh, emissions_weight):
    ordered = sorted(
        options,
        key=lambda option: (
            option.price_rmb_per_kwh
            + emissions_weight * option.emissions_kg_per_kwh,
            option.emissions_kg_per_kwh,
            option.price_rmb_per_kwh,
            option.source_id,
        ),
    )
    remaining = target_kwh
    amounts = {}
    for option in ordered:
        amount = min(option.availability_kwh, remaining)
        if amount > 1e-9:
            amounts[option.source_id] = amount
            remaining -= amount
        if remaining <= 1e-9:
            break
    if remaining > 1e-6:
        raise ValueError(
            f"Configured sources can supply only {target_kwh - remaining:.3f} "
            f"kWh, less than the {target_kwh:.3f} kWh target."
        )
    return amounts


def _candidate(options, amounts, target_kwh):
    by_id = {option.source_id: option for option in options}
    cost = sum(
        amount * by_id[source_id].price_rmb_per_kwh
        for source_id, amount in amounts.items()
    )
    emissions = sum(
        amount * by_id[source_id].emissions_kg_per_kwh
        for source_id, amount in amounts.items()
    )
    mix = "; ".join(
        f"{source_id}={amount / target_kwh * 100:.1f}%"
        for source_id, amount in sorted(amounts.items())
    )
    categories = "; ".join(
        f"{source_id}: {by_id[source_id].price_category}"
        for source_id in sorted(amounts)
    )
    return {
        "energy_kwh": target_kwh,
        "avg_price_rmb_per_kwh": cost / target_kwh,
        "emissions_kg_per_mwh": emissions / (target_kwh / 1000),
        "mix": mix,
        "price_categories": categories,
    }


def pareto_frontier(inputs, target_mwh=1.0):
    if target_mwh <= 0:
        raise ValueError("Target energy must be positive.")
    options, emission_unit = _procurement_options(inputs)
    target_kwh = target_mwh * 1000
    if sum(option.availability_kwh for option in options) + 1e-9 < target_kwh:
        available = sum(option.availability_kwh for option in options)
        raise ValueError(
            f"Configured sources can supply only {available:.3f} kWh, less "
            f"than the {target_kwh:.3f} kWh target."
        )

    breakpoints = {0.0}
    for index, first in enumerate(options):
        for second in options[index + 1:]:
            emissions_delta = (
                first.emissions_kg_per_kwh - second.emissions_kg_per_kwh
            )
            if abs(emissions_delta) <= 1e-12:
                continue
            weight = (
                second.price_rmb_per_kwh - first.price_rmb_per_kwh
            ) / emissions_delta
            if weight > 0:
                breakpoints.add(weight)
    ordered_breakpoints = sorted(breakpoints)
    weights = [0.0]
    weights.extend(
        (left + right) / 2
        for left, right in zip(ordered_breakpoints, ordered_breakpoints[1:])
    )
    weights.append(ordered_breakpoints[-1] + max(
        1.0, abs(ordered_breakpoints[-1])
    ))

    candidates = {}
    for weight in weights:
        amounts = _allocate(options, target_kwh, weight)
        point = _candidate(options, amounts, target_kwh)
        key = (
            round(point["avg_price_rmb_per_kwh"], 12),
            round(point["emissions_kg_per_mwh"], 12),
        )
        candidates.setdefault(key, point)

    points = list(candidates.values())
    frontier = [
        point for point in points
        if not any(
            other["avg_price_rmb_per_kwh"]
            <= point["avg_price_rmb_per_kwh"] + 1e-10
            and other["emissions_kg_per_mwh"]
            <= point["emissions_kg_per_mwh"] + 1e-10
            and (
                other["avg_price_rmb_per_kwh"]
                < point["avg_price_rmb_per_kwh"] - 1e-10
                or other["emissions_kg_per_mwh"]
                < point["emissions_kg_per_mwh"] - 1e-10
            )
            for other in points
        )
    ]
    frontier.sort(key=lambda point: (
        point["emissions_kg_per_mwh"],
        point["avg_price_rmb_per_kwh"],
    ))
    return frontier, emission_unit


def calendar_year_frontiers(inputs, target_mwh=1.0):
    """Find independent ordinary-operation procurement frontiers per year."""
    years = sorted({timestamp.year for timestamp in inputs.timestamps})
    points_by_year = []
    emission_units = set()
    for year in years:
        indices = [
            index for index, timestamp in enumerate(inputs.timestamps)
            if timestamp.year == year
        ]
        timestamps = tuple(inputs.timestamps[index] for index in indices)
        months = {timestamp.strftime("%Y-%m") for timestamp in timestamps}
        year_inputs = replace(
            inputs,
            timestamps=timestamps,
            protected_it_kw=tuple(
                inputs.protected_it_kw[index] for index in indices
            ),
            availability_kwh={
                source_id: tuple(values[index] for index in indices)
                for source_id, values in inputs.availability_kwh.items()
            },
            monthly_prices={
                key: price for key, price in inputs.monthly_prices.items()
                if key[1] in months
            },
        )
        points, emission_unit = pareto_frontier(year_inputs, target_mwh)
        emission_units.add(emission_unit)
        points_by_year.extend(
            {
                "year": year,
                "cost_rmb": round(
                    point["avg_price_rmb_per_kwh"] * point["energy_kwh"], 10
                ),
                **point,
            }
            for point in points
        )
    if len(emission_units) != 1:
        raise ValueError(
            "Cannot compare direct CO2 and lifecycle CO2e factors in one plot."
        )
    return points_by_year, next(iter(emission_units))


def write_frontier_csv(path, points, emission_unit, include_year=False):
    import csv

    emissions_column = (
        "emissions_kgCO2e_per_MWh"
        if emission_unit.startswith("kgCO2e")
        else "emissions_kgCO2_per_MWh"
    )
    fieldnames = [
        "energy_kwh",
        "avg_price_rmb_per_kwh",
        emissions_column,
        "mix",
        "price_categories",
    ]
    if include_year:
        fieldnames = ["year", "cost_rmb", *fieldnames]
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
            lineterminator="\n",
        )
        writer.writeheader()
        for point in points:
            row = {
                "energy_kwh": point["energy_kwh"],
                "avg_price_rmb_per_kwh": point["avg_price_rmb_per_kwh"],
                emissions_column: point["emissions_kg_per_mwh"],
                "mix": point["mix"],
                "price_categories": point["price_categories"],
            }
            if include_year:
                row["year"] = point["year"]
                row["cost_rmb"] = point["cost_rmb"]
            writer.writerow(row)


def write_frontier_svg(
    path, points, emission_unit, target_mwh, series_key=None
):
    width, height = 900, 560
    left, right, top, bottom = 105, 45, 70, 95
    plot_width = width - left - right
    plot_height = height - top - bottom
    x_values = [point["emissions_kg_per_mwh"] for point in points]
    y_values = [
        point.get("cost_rmb", point["avg_price_rmb_per_kwh"])
        for point in points
    ]
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(y_values), max(y_values)
    x_pad = (x_max - x_min) * 0.15 or max(abs(x_min) * 0.15, 0.05)
    y_pad = (y_max - y_min) * 0.15 or max(abs(y_min) * 0.15, 0.05)
    x_min, x_max = max(0.0, x_min - x_pad), x_max + x_pad
    y_min, y_max = y_min - y_pad, y_max + y_pad

    def x_map(value):
        return left + (value - x_min) / (x_max - x_min) * plot_width

    def y_map(value):
        return top + (y_max - value) / (y_max - y_min) * plot_height

    def point_cost(point):
        return point.get("cost_rmb", point["avg_price_rmb_per_kwh"])

    def esc(value):
        return (
            str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;")
        )

    emissions_name = (
        "kgCO2e" if emission_unit.startswith("kgCO2e") else "kgCO2"
    )
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
        f'height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#233044}'
        '.axis{stroke:#344054;stroke-width:1.5}'
        '.grid{stroke:#e5e7eb;stroke-width:1}'
        '.frontier{fill:none;stroke:#1976d2;stroke-width:3}'
        '.point{stroke:white;stroke-width:2}</style>',
        f'<text x="{width / 2}" y="32" text-anchor="middle" font-size="20">'
        f'Cost-emissions frontier for {target_mwh:g} MWh</text>',
    ]
    for step in range(6):
        fraction = step / 5
        x_value = x_min + fraction * (x_max - x_min)
        y_value = y_min + fraction * (y_max - y_min)
        x = x_map(x_value)
        y = y_map(y_value)
        lines.extend([
            f'<line class="grid" x1="{x:.2f}" y1="{top}" '
            f'x2="{x:.2f}" y2="{height - bottom}"/>',
            f'<text x="{x:.2f}" y="{height - bottom + 22}" '
            f'text-anchor="middle" font-size="12">{x_value:.3g}</text>',
            f'<line class="grid" x1="{left}" y1="{y:.2f}" '
            f'x2="{width - right}" y2="{y:.2f}"/>',
            f'<text x="{left - 12}" y="{y + 4:.2f}" '
            f'text-anchor="end" font-size="12">{y_value:.3g}</text>',
        ])
    lines.extend([
        f'<line class="axis" x1="{left}" y1="{height - bottom}" '
        f'x2="{width - right}" y2="{height - bottom}"/>',
        f'<line class="axis" x1="{left}" y1="{top}" '
        f'x2="{left}" y2="{height - bottom}"/>',
        f'<text x="{left + plot_width / 2}" y="{height - 28}" '
        f'text-anchor="middle" font-size="15">Emissions '
        f'({emissions_name}/MWh)</text>',
    ])
    y_axis_label = (
        f"Procurement cost (RMB for {target_mwh:g} MWh)"
        if series_key
        else "Average price (RMB/kWh)"
    )
    lines.append(
        f'<text x="25" y="{top + plot_height / 2}" '
        f'text-anchor="middle" font-size="15" '
        f'transform="rotate(-90 25 {top + plot_height / 2})">'
        f'{esc(y_axis_label)}</text>'
    )
    series = {}
    for point in points:
        name = str(point[series_key]) if series_key else ""
        series.setdefault(name, []).append(point)
    colors = ("#d84315", "#1976d2", "#388e3c", "#8e24aa", "#ef6c00")
    for series_index, (name, series_points) in enumerate(series.items()):
        color = colors[series_index % len(colors)]
        coordinates = " ".join(
            f'{x_map(point["emissions_kg_per_mwh"]):.2f},'
            f'{y_map(point_cost(point)):.2f}'
            for point in series_points
        )
        if len(series_points) > 1:
            lines.append(
                f'<polyline class="frontier" style="stroke:{color}" '
                f'points="{coordinates}"/>'
            )
        for index, point in enumerate(series_points, start=1):
            x = x_map(point["emissions_kg_per_mwh"])
            y = y_map(point_cost(point))
            label = f"{name + ' · ' if name else ''}{index}. {point['mix']}"
            lines.extend([
                f'<circle class="point" fill="{color}" cx="{x:.2f}" '
                f'cy="{y:.2f}" r="7"/>',
                f'<text x="{x + 11:.2f}" y="{y - 10:.2f}" '
                f'font-size="12">{esc(label)}</text>',
            ])
        if series_key:
            legend_y = 52 + series_index * 20
            lines.extend([
                f'<circle fill="{color}" cx="{width - right - 110}" '
                f'cy="{legend_y - 4}" r="5"/>',
                f'<text x="{width - right - 98}" y="{legend_y}" '
                f'font-size="12">{esc(name)}</text>',
            ])
    lines.append("</svg>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
