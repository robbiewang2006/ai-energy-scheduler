# ai-energy-scheduler

## Run the synthetic example

The default dataset is a small, explicitly hypothetical one-day scenario. It is
not official data and is not silently used as source-derived information:

```bash
python -m pip install -r requirements.txt
python scheduler.py
# Or select it explicitly:
python scheduler.py --data-dir examples/synthetic
```

The scheduler retains hourly job placement and dispatch. Input prices are
monthly: each source/month row is identified by `YYYY-MM`, and every hour in
that month uses the same configured price. Timestamps in the hourly data
select the applicable month, including across December/January. Use a complete
hourly horizon with one timestamp per hour.

## Constrained gradient scheduling

The default run now compares **gradient**, **immediate**, **price_aware**
(greedy cost-only), and **price_carbon_aware** (greedy cost plus carbon).
Every strategy uses identical jobs, CSV inputs, scenario, fixed PUE, and an
initially empty battery. All comparison scores use the same carbon weight,
including strategies that choose their allocations using cost alone.
NumPy and SciPy are the optimizer's numerical dependencies (Python 3.11+);
the CSV loader and original dispatch accounting are retained.

For flexible job allocations \(x_{j,t}\), the objective is

\[
 S(x)=C(x)+w E(x),\qquad w=0.20\ \mathrm{RMB/kg}\ \text{by default}.
\]

Here \(C\) is electricity cost in RMB and \(E\) is emissions in kg under a
consistent configured accounting boundary. The carbon penalty is an objective
term, **not an electricity bill**. Direct CO2 and lifecycle CO2e accounting
must not be mixed; the dispatch evaluator rejects inconsistent used factors.
Source-specific monthly prices, fuel conversions, scenario multipliers, and
price/emission metadata continue to come from the input bundle.

The computing feasible set is

\[
 x_{j,t}\ge0,\quad x_{j,t}=0\ (t\notin[r_j,d_j)),\quad
 \sum_t x_{j,t}=e_j,\quad
 p_t+\sum_j x_{j,t}\le35\ \mathrm{kW}.
\]

Each time step is one hour, so allocations in kWh correspond numerically to
average kW. Protected IT demand \(p_t\) is unchanged and PUE stays fixed at 1.2.
HiGHS first checks the **joint** computing constraints. Each gradient step is
projected by solving the convex quadratic problem
\(\min_{z\in\mathcal C}\frac12\|z-y\|^2\) with SLSQP, analytic projection
derivatives, linear job equalities, shared hourly inequalities, and nonnegative
bounds. This is not independent clipping. Projection failure is an error, not
permission to return an incomplete job.

### Gradients, feasibility, and stopping

This is a small, dense prototype, not an annual-scale optimizer. The original
`dispatch()` remains the evaluator: onsite solar first, solar-only charging,
stateful battery discharge, then other sources in their configured priority
order, with source power, availability and fuel limits. Every evaluation
restarts the battery empty. No automatic differentiation or smoothing of this
stateful, piecewise-linear model is assumed.

Central finite differences use two dispatch evaluations per allocation. At a
nonnegativity or supply boundary, an evaluable one-sided difference is used.
If neither side is evaluable the partial is zero; that can stall and does not
certify stationarity. Probes may temporarily violate computing constraints
to estimate the dispatch function's partial derivatives, but accepted
schedules must satisfy all computing constraints. Accounting errors are not
treated as shortages or hidden by a penalty.

Internally, allocations are divided by the maximum spare hourly IT capacity
(at least 1 kWh), and the objective is divided by fixed facility energy
(at least 1 kWh). The finite-difference step and learning rate operate in
these scaled coordinates; reported costs and emissions remain unscaled.
Backtracking halves the learning rate (up to 30 trials) until the original
dispatch supplies the candidate and its score does not increase. Supply
feasibility is checked at every trial, not approximated by a soft penalty.
The best feasible schedule is retained.

Initialization choices are projected uniform (default), seeded projected
random, immediate, or greedy. The last two use the original baseline builders.
A computing-feasible initialization may still fail original dispatch under
an outage or fuel shortage. In that case the optimizer stops explicitly:
**failure to initialize is not proof that no supply-feasible schedule exists**.
Try another initialization. The greedy baseline skips supply-infeasible trial
allocations, but can still fail to find a complete placement. Neither a failed
heuristic nor a failed initialization proves global infeasibility. Baseline
comparisons are still exported when gradient initialization fails.
The prototype does not redesign supply
dispatch or promise to find a feasible seed for every input.

Stopping occurs at the iteration limit, failed backtracking, or when scaled
maximum allocation movement or scaled score improvement is at most the
convergence tolerance. Flat regions, nonsmooth boundaries and local optima
are possible; there is **no global-optimality guarantee**. A positive printed
“Gradient minus greedy score” means gradient descent performed worse.

```bash
python scheduler.py --data-dir examples/synthetic --scenario ordinary \
  --carbon-weight 0.20 --learning-rate 1.0 --max-iterations 100 \
  --tolerance 1e-7 --finite-difference-step 1e-5 \
  --initialization uniform --seed 0 --output-dir results
python -m unittest discover -s tests -p test_optimizer.py
python -m unittest discover -s tests
```

### Normalized scenario metrics, not annual forecasts

For completed jobs and served protected demand, fixed PUE makes

\[
 F=\sum_t\mathrm{facility\_kWh}_t
   =\mathrm{PUE}\left(\sum_t p_t+\sum_j e_j\right)
\]

constant across candidates. Battery charging is **not additional end-use
facility demand**; grid imports are **not** the denominator. For the default
reference \(R=1{,}000{,}000{,}000\) kWh (1 TWh),

\[
 a=R/F,\quad C_R=aC,\quad E_R=aE,\quad S_R=C_R+wE_R.
\]

`--annual-demand-reference-kwh` changes \(R\), not the workload, dispatch, or
optimizer's internal objective scaling. `*_per_reference_*` columns use that
configured value, while `*_per_1twh_*` columns always use exactly 1 TWh.
Zero facility energy cannot be normalized. These are **normalized scenario
metrics**, not a verified annual forecast: a 24-hour run remains a 24-hour
simulation. Cross-month horizons use each timestamp's applicable monthly price.

The output directory contains:

- `<strategy>_hourly.csv`: final job allocations and original dispatch,
  protected/facility demand, source use, battery flows/SOC, prices and factor
  metadata.
- `optimization_history.csv`: initial and accepted iteration cost, emissions,
  score, maximum constraint violation in kWh, feasibility, stopping status,
  and number of supply-infeasible backtracking trials.
- `comparison.csv`: separate raw cost, emissions and score; facility/grid
  energy; normalization factor and reference; normalized cost, emissions and
  score; simulation dates/hours; maximum constraint violation and feasibility.
  Failed baselines are explicitly marked, without fabricated metrics.

Tests include joint job/window/capacity feasibility, original-dispatch supply
balance and battery conservation, finite-difference checks in linear regions,
seed reproducibility, normalization, invalid inputs, and a two-hour
cross-month example compared with every integer allocation (whose cheapest
endpoint is also the continuous optimum).

## Supply model and inputs

The input directory has four required CSV files plus an optional scenario file:

- `source_config.csv`: configured source ID, technology, supply type, price
  unit, power capacity, dispatch order, and (for an onsite diesel generator)
  fuel consumption in L/kWh and inventory in litres.
- `monthly_prices.csv`: one row per configured source and calendar month, with
  months recorded as `01` through `12` and no year. This monthly profile repeats
  for each year in the horizon. Its columns are `source_id`, `month`, `price`,
  and `unit`. Electricity prices are RMB/kWh; diesel fuel prices are RMB/litre.
  Negative electricity prices are valid. Missing prices must be recorded with a
  blank value; the simulator errors if it would use that source rather than
  treating the price as zero.
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

Use `data/` as a template for source-derived records; see
[`data/README.md`](data/README.md) for candidate authorities, data gaps,
boundaries, and provenance requirements. Do not fill gaps with a retail tariff
labeled as a technology price or present assumptions as observations. If a
verified fixed-price contract is repeated month to month, retain its supporting
source outside the compact price table; otherwise treat repeated values as a
hypothetical scenario assumption. A monthly diesel value derived from
effective-date price notices must use a day-weighted method and remains distinct
from an actual transaction price.

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
