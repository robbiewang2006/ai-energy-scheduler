# ai-energy-scheduler

The scheduler uses built-in synthetic hourly data by default:

```bash
python scheduler.py
```

To provide hourly data from a CSV, pass `--input-csv`. The CSV must contain
exactly 24 hours (numbered 0 through 23) and these columns:
`hour`, `price_rmb_per_kwh`, `solar_kw`, `grid_co2_kg_per_kwh`, and
`protected_it_kw`. Negative electricity prices are allowed; solar, grid carbon
intensity, and protected IT demand must be nonnegative. See
[`examples/synthetic_inputs.csv`](examples/synthetic_inputs.csv) for a complete
example.

```bash
python scheduler.py --input-csv examples/synthetic_inputs.csv
```

Run the automated tests with:

```bash
python -m unittest discover -s tests
```