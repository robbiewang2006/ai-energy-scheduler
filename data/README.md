# Data provenance and limitations

These CSVs record input templates and explicit data gaps, not observed numeric
prices or emissions factors. The official-site requests made for this change
failed at DNS lookup, so no source-derived numeric prices or factors could be
verified and none are filled in here. `monthly_prices.csv` has a blank
`status=missing` row for each configured source/month for 2025; this is a
missing-value marker, not zero. `source_config.csv` uses zero-capacity
placeholders that must be replaced. This includes source-specific monthly
contracts, Shanghai tariffs and diesel prices, and the underlying official
emissions-factor table.

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
`customer_purchase_price`; it must not be described as a solar, nuclear, coal,
gas, hydro, wind, or diesel-generator electricity price. Diesel notices publish
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

`source_config.csv` records supply type (`onsite`, `contract`, or `grid`),
power capacity, dispatch order, and optional diesel fuel constraints. Its
placeholder capacities are not operational limits or observations.
`hourly_availability.csv` is separate from monthly prices and records the
timestamped available kWh and protected IT demand. Onsite solar must have zero
availability at night. Contracts require a delivery profile; grid import is
limited by both its configured capacity and hourly availability. Use only one
ordinary-grid source: do not add the grid's generation mix again as separate
grid supply. The energy-source list is configurable; nuclear in a contract is
not evidence of an onsite reactor or any Huawei ownership.

The runnable, explicitly hypothetical inputs are in
[`../examples/synthetic`](../examples/synthetic/). They are not official data
and must not be used as a substitute for verified prices or emissions factors.
