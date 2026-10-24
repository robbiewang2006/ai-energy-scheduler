# ai-energy-scheduler

## Run the synthetic example

The default dataset is a small, explicitly hypothetical one-day scenario. It is
not official data and is not silently used as source-derived information:

```bash
python scheduler.py
# Or select it explicitly:
python scheduler.py --data-dir examples/synthetic
```

The scheduler retains hourly job placement and dispatch. Input prices are
monthly: each source/month row is identified by `YYYY-MM`, and every hour in
that month uses the same configured price. Timestamps in the hourly data
select the applicable month, including across December/January. Use a complete
hourly horizon with one timestamp per hour.

## Supply model and inputs

The input directory has four required CSV files plus an optional scenario file:

- `source_config.csv`: configured source ID, technology, supply type, price
  unit, power capacity, dispatch order, and (for an onsite diesel generator)
  fuel consumption in L/kWh and inventory in litres.
- `monthly_prices.csv`: one row per configured source and each calendar month
  in the hourly horizon. Electricity prices are RMB/kWh; diesel fuel prices
  are RMB/litre. Price category distinguishes customer purchase price,
  generator selling price, fuel price, estimated generation cost/LCOE, and
  explicit user assumption. Negative electricity prices are valid. Missing
  prices must be recorded with a blank value and `status=missing`; the
  simulator errors if it would use that source rather than treating the price
  as zero.
- `hourly_availability.csv`: hourly timestamp, source ID, available kWh, and
  protected IT demand. This separate profile constrains source delivery in
  addition to configured capacity. Set onsite PV output to zero at night.
- `emission_factors.csv`: one factor per source with units, reference year,
  geography, and accounting boundary. Direct factors (`kgCO2/...`) and
  lifecycle factors (`kgCO2e/kWh`) cannot be mixed in a single objective.
- `scenario_impacts.csv`: optional per-scenario, per-source multipliers for
  available energy and price. Unlisted sources are unchanged; `ordinary` is the
  implicit baseline. Set grid availability to zero for an outage, and model
  import bottlenecks (including concentrated natural-gas supply) by reducing
  affected sources' availability and/or increasing their price. Scenario values
  are explicit assumptions, not automatic geopolitical forecasts.

`supply_type` must be `onsite`, `contract`, or `grid`; grid import is optional.
Keep source-specific contracts distinct from an ordinary grid source, and do
not add generation technologies as extra grid supply. Source IDs and
technologies are configurable: solar PV, nuclear, natural-gas-fired electricity,
wind, and onsite diesel can be represented; additional technologies can be
added by configuration. The `data/` template lists source types without an
ordinary-grid entry. The runnable synthetic example still demonstrates onsite
PV and ordinary-grid supply. A nuclear contract is not an onsite reactor and
makes no claim about Huawei asset ownership. Corporate annual renewable
percentages are not hourly source-availability profiles.

The technology identifiers used by the templates include `solar_pv`, `wind`,
`nuclear`, `natural_gas_fired`, and `diesel_generator`; `ordinary_grid` remains
available for optional imports.
Other non-grid identifiers may be added in `source_config.csv`.

Diesel prices remain in RMB/litre and are multiplied by the configured
generator fuel rate (L/kWh); diesel emissions use kgCO2/litre and the same
fuel rate. `fuel_litres_to_electricity_kwh()` converts reserve fuel volume to
electric output using that generator-specific rate, and dispatch outputs the
equivalent scenario-adjusted RMB/kWh price. The model does not invent a
universal generator consumption rate and limits generation by hourly
availability/capacity and configured diesel inventory. Give strategic reserves
a later `dispatch_order` than normal supply so they are used only when needed.
The battery retains its original solar-only charging behavior.

Run a configured emergency case with:

```bash
python scheduler.py --data-dir /tmp/my-energy-inputs --scenario grid_outage
```

Scenario impacts do not assign source countries or dependency shares: set
source-specific assumptions from verified procurement and supply-chain data.
For example, a `gas_supply_shock` scenario can reduce availability for a
Russian-origin gas contract while leaving other sources unchanged; a
`hormuz_disruption` scenario can affect only sources whose documented supply
routes are exposed.

## Replacing the example data

Copy `examples/synthetic/` to a new directory, replace its values, and run:

```bash
cp -R examples/synthetic /tmp/my-energy-inputs
# Edit source_config.csv, monthly_prices.csv, hourly_availability.csv,
# and emission_factors.csv, then:
python scheduler.py --data-dir /tmp/my-energy-inputs
```

Use `data/` as a template for source-derived records. Those CSVs are
intentionally empty because official source pages could not be fetched during
this work; see [`data/README.md`](data/README.md) for candidate authorities,
data gaps, boundaries, and provenance requirements. Do not fill gaps with a
retail tariff labeled as a technology price or present assumptions as
observations. If a verified fixed-price contract is repeated month to month,
record the supporting source; otherwise label repeated values as a hypothetical
scenario assumption. A monthly diesel value derived from effective-date price
notices must state its day-weighted method and remains distinct from an actual
transaction price.

Outputs are written to `results/`. Run automated tests with:

```bash
python -m unittest discover -s tests
```

## Annual 1 MWh cost-emissions frontiers

Plot each calendar year's feasible source mixes for 1 MWh, using monthly
prices and hourly availability from that year only:

```bash
python plot_frontier.py
# Choose another dataset or target:
python plot_frontier.py --data-dir /tmp/my-energy-inputs --target-mwh 2
```

The command writes `results/emissions_price_frontier.svg` and
`results/emissions_price_frontier.csv`. Each year is optimized independently;
colors and the CSV `year` column identify its nondominated points. The plot
shows total procurement cost in RMB for the selected MWh target against
emissions in kgCO2(e)/MWh; the CSV also includes average RMB/kWh. It uses
ordinary configured source availability and prices only; disruption scenarios
are intentionally excluded. Source/month rows with a missing price
or source emission factor are omitted rather than assigned zero. If configured
available energy in any year is insufficient for the target, the command fails
instead of extrapolating availability.

The bundled synthetic profile has one day of data and only two sources; its
onsite solar supply is availability-limited, so it produces one annual frontier
point for the best feasible mix, not a rich market-wide curve. The prices and
emissions are hypothetical explicit assumptions. Different price categories
(such as customer tariffs, generator selling prices, and generation cost/LCOE)
remain identified in the CSV and should not be interpreted as
interchangeable observed market prices. The chart is an energy procurement
comparison over the supplied horizon, not a battery dispatch or grid-mix
analysis. For a multi-year comparison, provide a contiguous hourly profile
covering each year to be plotted.

The checked-in [synthetic example plot](examples/synthetic/results/emissions_price_frontier.svg)
and [frontier data](examples/synthetic/results/emissions_price_frontier.csv)
were generated from that same hypothetical one-day input bundle.
