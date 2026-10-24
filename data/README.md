# Data provenance and limitations

These CSVs contain user-provided assumptions and explicit data gaps, not a
complete set of verified market prices or emissions factors. `monthly_prices.csv`
contains only `source_id`, `month` (`01`–`12`), `price`, and `unit`; month values
have no year and repeat for each year in the simulation horizon. The 2025 wind
prices are user-provided values converted from 元/MWh to RMB/kWh and are not
independently verified. The onsite-solar prices are unweighted averages of the
user-provided Shandong, Shanxi, and Sichuan values, converted from RMB/MWh to
RMB/kWh; they are not independently verified or Shanghai-specific. The 2025
natural-gas-fired prices are user-provided estimates with unspecified
estimation method and geography, not verified market prices. Nuclear prices
are the user-provided 0.3932 RMB/kWh assumption for every month. Per user
request, the 2025 estimated weighted fuel costs from the supplied image are also
entered as the `onsite_diesel` prices in `monthly_prices.csv`. They are
unverified aggregate assumptions, not source-specific generator costs; their
weighting method and geography are unspecified. The monthly profile has no year
and repeats for every simulation year. The original 2025 values and provenance
are retained in `estimated_weighted_fuel_cost.csv`. Other source-specific prices
remain blank; this is a missing value marker, not zero.

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

`monthly_prices.csv` has one record per configured source and month (`01`–`12`),
with no year. Use a blank `price` to record a data gap; the simulator refuses
to dispatch a source with a missing price. Electricity uses RMB/kWh; diesel
uses RMB/litre and is converted using the configured litres/kWh rate.

For each verified or derived price, retain its publisher, source URL, table/title,
publication date, applicable period, geography, original units, and conversion
or aggregation method outside the compact price table. Emission rows document
kgCO2/kWh (direct operational CO2), kgCO2e/kWh (lifecycle), or kgCO2/litre
(direct diesel combustion), with reference year, geography, boundary, source,
and method. Direct and lifecycle factors cannot be combined in one objective.

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
