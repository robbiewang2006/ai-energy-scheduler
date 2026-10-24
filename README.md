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

The input directory has four CSV files:

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

`supply_type` must be `onsite`, `contract`, or `grid`. The one
`ordinary_grid` supply is distinct from source-specific contracted electricity
and onsite generation. Do not add grid technologies (such as coal or nuclear
generation mix) as extra grid supply. Source IDs and technologies are
configurable: solar PV, hydroelectric, nuclear, coal-fired electricity,
natural-gas-fired electricity, wind, and onsite diesel can be represented;
additional technologies can be added by configuration. The example configures
onsite PV and ordinary grid only. A nuclear contract is not an onsite reactor
and makes no claim about Huawei asset ownership. Corporate annual renewable
percentages are not hourly source-availability profiles.

The example technology identifiers are `solar_pv`, `hydroelectric`, `nuclear`,
`coal_fired`, `natural_gas_fired`, `wind`, `diesel_generator`, and
`ordinary_grid`. Other non-grid identifiers may be added in `source_config.csv`.

Diesel prices remain in RMB/litre and are multiplied by the configured
generator fuel rate (L/kWh); diesel emissions use kgCO2/litre and the same
fuel rate. The model does not compare RMB/litre to electricity RMB/kWh, does
not invent a universal generator consumption rate, and limits generation by
both hourly availability/capacity and configured diesel inventory. The battery
retains its original solar-only charging behavior.

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
