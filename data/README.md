# Data provenance and limitations

These CSVs contain user-provided assumptions and explicit data gaps, not a
complete set of verified market prices or emissions factors. Monthly prices
cover 2025–2026. The 2025 wind prices are user-provided values converted from
元/MWh to RMB/kWh; their provenance and method are recorded in each row, but
they are not independently verified. The onsite-solar prices are unweighted
averages of the user-provided Shandong, Shanxi, and Sichuan values, converted
from RMB/MWh to RMB/kWh; they are not independently verified or
Shanghai-specific. The 2025 natural-gas-fired prices are user-provided
estimates with unspecified estimation method and geography, not verified
market prices. Nuclear prices for 2026 are the user-provided 0.3932 RMB/kWh
assumption for every month; 2025 nuclear prices remain missing. The 2025
aggregate estimated weighted fuel costs are recorded separately in
`estimated_weighted_fuel_cost.csv`; their weighting method and geography are
unspecified, so they are not source-specific dispatch prices. Other
source-specific prices remain blank with `status=missing`; this is a missing
value marker, not zero.

The lifecycle emissions values in `emission_factors.csv` were provided as
general DeepSeek data and converted from gCO2e/kWh to kgCO2e/kWh. Their
source URLs and conversions are recorded per row; they are assumptions and
have not been independently verified. The diesel template uses the supplied
electricity-output factor and is disabled until a source-specific capacity and
price are configured. `source_config.csv` uses zero-capacity placeholders
that must be replaced. The official sources and emissions references have not
been independently verified.

Start verification with:

- Shanghai Municipal Development and Reform Commission:
  <https://fgw.sh.gov.cn/> (search for 上海市电价表 / 销售电价 and the effective
  date). A customer tariff is not a source-specific generator price.
- National Development and Reform Commission:
  <https://www.ndrc.gov.cn/> (market-operation disclosures and 成品油价格调整).
- National Energy Administration: <https://www.nea.gov.cn/>.
- Ministry of Ecology and Environment: <https://www.mee.gov.cn/>.
- National Bureau of Statistics: <https://www.stats.gov.cn/>.

An official joint announcement titled “关于发布2022年电力二氧化碳排放因子的公告”
was identified as a candidate emissions source. Values reported in secondary
search results (including 0.5366 kgCO2/kWh national average and
0.5854 kgCO2/kWh East China regional) could not be checked against that
announcement and are deliberately not entered as verified data. East China is
not a Shanghai-specific factor. Do not use the fossil-electricity factor as a
grid import factor.

No comparable source-classified Shanghai monthly price series could be
verified. A Shanghai retail tariff, if later entered, must use
`customer_purchase_price`; it must not be described as a source-specific
generator electricity price. Diesel notices publish
maximum retail prices with effective dates, not actual transaction prices or
monthly averages. A day-weighted average of those notices is derived data and
must be documented as such.

For direct diesel combustion, the IPCC 2006 Guidelines report an energy-based
default of 74,100 kgCO2/TJ for gas/diesel oil. It is not a kgCO2/litre factor;
conversion requires a documented fuel heating value and density. The project's
input template does not invent that conversion or a generator's L/kWh rate.
Use the official specification for the actual generator, including its load
basis.

## Populating the templates

Keep the documented columns and units. `monthly_prices.csv` has one record per
configured source and `YYYY-MM` month. Use a blank `price` with `status=missing`
to record a data gap; the simulator refuses to dispatch a source with a missing
price. `price_category` distinguishes customer purchase prices, generator
selling prices, fuel prices, estimated generation cost/LCOE, and explicit user
assumptions. Electricity uses RMB/kWh; diesel uses RMB/litre and is converted
using the configured litres/kWh rate.

For each verified or derived price, include publisher, source URL, table/title,
publication date, applicable period, geography, original units, and the
conversion or aggregation method. Emission rows must document kgCO2/kWh (direct
operational CO2), kgCO2e/kWh (lifecycle), or kgCO2/litre (direct diesel
combustion), with reference year, geography, boundary, source, and method.
Direct and lifecycle factors cannot be combined in one objective.

`source_config.csv` records supply type (`onsite`, `contract`, or optional
`grid`), power capacity, dispatch order, and optional diesel fuel constraints. Its
placeholder capacities are not operational limits or observations.
`hourly_availability.csv` is separate from monthly prices and records the
timestamped available kWh and protected IT demand. Onsite solar must have zero
availability at night. Contracts require a delivery profile; grid import, if
configured, is limited by both its configured capacity and hourly availability.
Keep the grid's generation mix separate from source-specific contracts. The
energy-source list is configurable; nuclear in a contract is not evidence of
an onsite reactor or any Huawei ownership.
- `scenario_impacts.csv` records optional, explicitly assumed source-specific
availability and price multipliers for outages or supply disruptions. Use
verified exposure data to configure shocks to concentrated gas imports or
routes affected by a Hormuz-like event; the model does not infer geopolitical
dependencies.

The runnable, explicitly hypothetical inputs are in
[`../examples/synthetic`](../examples/synthetic/). They are not official data
and must not be used as a substitute for verified prices or emissions factors.
