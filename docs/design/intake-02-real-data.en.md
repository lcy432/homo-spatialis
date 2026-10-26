# intake-02: Real data and the opportunity database

English · [简体中文](intake-02-real-data.md)

The intake layer turns public source files into traceable records, mapped opportunities and queryable catalogs. The executable life pack is a separate downstream product. Start with [Data and mapping](../DATA.en.md) for the overall approach.

## Scope and geography

The current validation set covers six US metropolitan areas under the 2023 CBSA definitions:

| Metropolitan area | CBSA |
|---|---|
| New York | 35620 |
| Los Angeles | 31080 |
| San Francisco–Oakland–Fremont | 41860 |
| Chicago | 16980 |
| Houston | 26420 |
| Seattle | 42660 |

San Francisco does not include San Jose. A municipality, metropolitan area and PUMA are different geographic units. OSM venue queries use the principal city's administrative boundary, while housing and labor statistics use their stated metropolitan or survey geography. Each record preserves that distinction.

The target monetary basis is `USD_2024_LOCAL`. Values in other bases retain their original basis until a documented conversion is available.

## Sources

| Source | Main use |
|---|---|
| Census geography and ACS PUMS | Geographic crosswalks, housing, work hours and commuting |
| ACS published tables | Independent aggregate comparisons and city background |
| BLS OEWS and employment projections | Occupational wages, employment and typical entry requirements |
| BLS CPI, Average Prices and Consumer Expenditure tables | Time conversion, ingredient prices and expenditure references |
| BEA Regional Price Parities | Spatial price adjustments |
| OpenStreetMap | Restaurant and activity-location evidence |
| ENERGY STAR | Appliance model and certification facts |
| IPEDS and CIP–SOC crosswalks | Training programs, fees and related occupations |
| Chicago Park District | Activity prices, calendars and eligibility |
| EPA AQI | Air-quality background |

The frozen [source inventory](../../data/frozen/intake/us-v1/reports/sources.json) and [coverage report (Chinese)](../../data/frozen/intake/us-v1/coverage.md) retain access failures and gaps. Later life-pack additions, including tax and safety evidence, are described in [Life-pack assembly](e3b-life-pack.en.md).

OSM data are © OpenStreetMap contributors and licensed under ODbL. The repository's Apache-2.0 license does not replace source-data licenses. See [Data licensing](../DATA.en.md).

## Provenance and units

Each standardized record carries the source identifier, raw row locator, file SHA-256, observation period and geography. Each field preserves raw and normalized values, units, monetary basis, transformation version and evidence type:

- `observed`: reported by the source.
- `derived`: obtained by a documented deterministic transformation.
- `estimated`: inferred using an estimation rule.
- `assumption`: supplied by the model.

Missing, suppressed, unmappable and access-restricted values retain their status and reason. None is silently converted to zero.

Raw cache reads are bound to the fetch record and SHA-256. Changed content requires a new build; an old manifest cannot certify a new file. Fetch timestamps and local paths do not define content identity.

CPI converts observation periods. RPP can estimate a local price from a broader geographic reference. A price index is a conversion factor, never the price of an executable purchase.

## Survey mappings

Housing uses a new-market proxy: vacant units offered for rent plus renter households that moved within the preceding twelve months. This combines asking and recently agreed rents; it is not a complete listing database.

Survey weights define conditional distributions. Small cells fall back to broader groups, with the chosen group and sample size recorded. Official ACS state-specific top codes are treated as censoring. A replacement top-code value is not the individual unit's true rent.

For ACS replicate estimates, the standard error is:

$$
SE(\hat\theta)=
\sqrt{\frac{4}{80}\sum_{r=1}^{80}
(\hat\theta_r-\hat\theta)^2}.
$$

Each replicate must use the same estimator as the main estimate. A step median cannot be paired with the standard error of an interpolated median. Comparisons against published ACS medians check filters and weighting; they do not establish that the new-market proxy equals current advertised rents.

The current executable housing and job distributions are described in [A/B mappings](e3b-mapping-and-daily-support.en.md). Earlier three-point housing menus remain part of the intake history. Intake now retains occupational source facts rather than generating the obsolete three-tier job menu.

## Opportunity states

An opportunity separately records origin, supply status, data sufficiency, sampling weight, monetary facts and compatibility with the engine. The compilation classification distinguishes:

| State | Meaning |
|---|---|
| Compilable | Required facts and engine representation are available |
| Data gap only | The engine can represent it, but required evidence is missing |
| Engine gap only | Evidence exists, but the engine cannot express the necessary conditions |
| Both gaps | Both kinds of work are needed |
| Reference fact | Useful context rather than an executable action |

A real activity with dates, age restrictions or term enrollment cannot be exported as an unrestricted monthly activity. A real appliance model without a price or installation facts is not automatically an executable purchase. Counts of overlapping gap reasons must not be added as though they were mutually exclusive opportunities.

## Build and query

`scripts/intake_data.py fetch` is the explicit network step. Build, export and queries use cached or frozen data:

~~~bash
uv run python scripts/intake_data.py build
uv run python scripts/intake_data.py export
uv run python scripts/intake_data.py records --sector jobs
uv run python scripts/intake_fixtures.py
~~~

The build graph coordinates configuration, fetching records, standardization, mapping, catalog assembly, checks and freezing. Stages can be reused only when their content identities agree. The local SQLite database and raw downloads are not bundled with the repository; committed manifests and reports document what was frozen.

## Code and verification

| Responsibility | Entry point |
|---|---|
| Contracts | [intake_contracts.py](../../src/common/intake_contracts.py) |
| Cross-object checks | [intake_data.py](../../src/ledger/intake_data.py) |
| Fetching, normalization, mapping and queries | [realdata](../../src/product/realdata/) |
| Build coordination | [data_build.py](../../src/workflows/data_build.py) |
| Offline checks | [Real-data tests](../../tests/product/realdata/) |

Contributors should provide source records and reproducible transformations following the [contribution guide](../../CONTRIBUTING.md). The common city-submission workflow is planned for V1.1.
