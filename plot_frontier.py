import argparse
from pathlib import Path

from energy_data import load_energy_inputs
from frontier import (
    calendar_year_frontiers,
    write_frontier_csv,
    write_frontier_svg,
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Plot annual procurement frontiers (default target: 1 MWh)."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "examples" / "synthetic",
        help="directory containing the four energy input CSV files",
    )
    parser.add_argument(
        "--target-mwh",
        type=float,
        default=1.0,
        help="energy procurement target in MWh (default: 1)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results"),
        help="directory for the SVG plot and frontier CSV",
    )
    args = parser.parse_args(argv)

    inputs = load_energy_inputs(args.data_dir)
    points, emission_unit = calendar_year_frontiers(inputs, args.target_mwh)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    svg_path = args.output_dir / "emissions_price_frontier.svg"
    csv_path = args.output_dir / "emissions_price_frontier.csv"
    write_frontier_svg(
        svg_path, points, emission_unit, args.target_mwh, series_key="year"
    )
    write_frontier_csv(csv_path, points, emission_unit, include_year=True)
    print(
        f"Wrote {len(points)} annual frontier point(s) for "
        f"{args.target_mwh:g} MWh across "
        f"{len({point['year'] for point in points})} year(s) to "
        f"{svg_path} and {csv_path}."
    )
    for point in points:
        print(
            f"{point['year']} | {point['emissions_kg_per_mwh']:.3f} "
            f"{emission_unit.split('/')[0]}/MWh "
            f"| {point['avg_price_rmb_per_kwh']:.4f} RMB/kWh "
            f"| {point['mix']}"
        )


if __name__ == "__main__":
    main()
