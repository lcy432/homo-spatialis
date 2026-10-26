# Data and mapping

English · [简体中文](DATA.md)

City statistics must be turned into homes, jobs, travel, and purchases an agent can choose and the engine can execute. The pipeline is **sources → standardized records → mappings and candidate pools → catalogs and annual menus → visible facts → execution records**. Each stage retains source and rule identities for tracing and independent testing.

## Current coverage

The US pack uses 2023 CBSA geography, further restricted by supported residence/work tax rules. Displayed city names are shorthand.

| City | CBSA | Simulation geography |
|---|---|---|
| New York | 35620 | Residence in the five NYC boroughs; work in the metropolitan area's New York State portion |
| Los Angeles | 31080 | Los Angeles metropolitan area |
| San Francisco | 41860 | San Francisco metropolitan area, excluding San Jose |
| Chicago | 16980 | Illinois portion of the metropolitan area |
| Houston | 26420 | Houston metropolitan area |
| Seattle | 42660 | Seattle metropolitan area |

Amounts use **2024 USD at local prices**, `USD_2024_LOCAL`, internally in cents. Sources have their own observation periods; download dates do not redefine those periods. See the [source manifest](../configs/intake/us_v1.yaml) and [six-city configuration](../configs/product/e3b_us_v1_pack.yaml).

Background statistics can cover different geographies. New York crime data covers the metropolitan area's New York State portion; Chicago metropolitan statistics can include counties outside simulated residence coverage. Reports and user/agent descriptions retain these differences.

Sources include Census ACS/PUMS and geography, BLS wages/consumption/time-use/prices, BEA regional price parities, official tax, air-quality and crime data, and local transport, training, and activity sources. Exact tables, fields, and versions are in the [source report](../data/frozen/intake/us-v1/reports/sources.json).

## Evidence and assumptions

A single catalog item can contain fields with different evidential status. Observed wages do not make job access, experience, or satisfaction parameters observed facts.

| Type | Meaning | Examples |
|---|---|---|
| Observed | Published or recorded directly | ACS rents, OEWS wage quantiles, tax rates, transit fares |
| Derived | Calculated or grouped by explicit rules | Weighted housing distributions, tax liabilities, round-trip minutes |
| Estimated or proxy | Imputation, distribution reconstruction, indirect measures | Utilities imputation, wage support points, service costs inferred from spending statistics |
| Model assumption | Relationships or defaults chosen to make simulation executable | Resource budgets, wage/location combinations, score anchors, behavior rules |

These distinctions are recorded in provenance, build reports, and the [parameter register](design/parameter-assumption-register.en.md). `real_data_frozen` binds frozen real sources and mappings; life rules and scores can still contain exploratory assumptions.

## Standardization

Acquisition stores source URLs, versions, access records, and content fingerprints. Standardization aligns geography, units, currency, period, and statistical population while preserving provenance. Building reads acquired inputs separately, allowing the same pack to be rebuilt.

Price indices, expenditure totals, and actual quotes serve different purposes. An index may support an explicit adjustment but is not an item's payment price. Taxes use supported residence, work location, and tax year. The pack does not perform implicit currency or base-year conversion. See [data intake](design/intake-02-real-data.en.md).

<a id="mapping-methods"></a>
## Two mapping methods

A starts from shared option structures and calibrates parameters with city evidence. B starts from statistical records and constructs candidate distributions while retaining attribute relationships. Both produce executable opportunities, but estimate different objects.

| | A: shared structure and calibration | B: empirical and conditional distributions |
|---|---|---|
| Evidence | Cost components, grouped statistics, comparable local quotes | Microdata, survey weights, wage quantiles |
| Uses | Food, services, daily support, consumption baskets, equipment, activities | Housing and jobs' wages, hours, and locations |
| Calculation | Cost prediction; Bayesian updates for supported parameters, currently retail-price correction | Marginal/conditional estimation, within-record relationships, declared factorization |
| Assumptions | Coverage, cost model, prior, observation errors, comparable quotes | Target population, sparse-cell fallback, conditional independence, censoring, unidentified mass |

These distributions describe available opportunities. They are not the LLM's personal choice probabilities.

<a id="mapping-a"></a>
### A: shared options and local parameter posteriors

Define common option families, valid combinations, functions, and units. For example, food plans combine home-cooked, dine-in, and delivered meals; consumption combines quantities of common reference baskets. Wages, regional price adjustments, and cost formulas give a base price for each specification.

$$
p(\theta_c\mid D_c)\propto p(D_c\mid\theta_c)\,p(\theta_c).
$$

The implemented Bayesian update corrects the price of a comparable retail specification relative to its base prediction. Wage and RPP inputs update costs; they do not automatically update service effectiveness, supply probabilities, or user preferences.

$$
\delta=\log(P_{\mathrm{actual}}/P_{\mathrm{base}}),
\qquad \delta\sim\mathcal N(0,\tau_0^2),
\quad \tau_0=\log(1.5).
$$

Quotes must match specification, pricing period, tax, and bundle conventions. Group them by supplier: multiple quotes from one supplier contribute one average log ratio, not multiple independent observations. With supplier mean and count n:

$$
\bar y\mid\delta\sim\mathcal N(\delta,v),\qquad
v=\begin{cases}
\log(1.5)^2,&n=1,\\
\max(s_y^2/n,\log(1.1)^2),&n\ge2.
\end{cases}
$$

The single-supplier variance and variance floor are assumptions. The computed v is treated as given, without its own posterior. Normal conjugacy yields:

$$
\tau_{\mathrm{post}}^2=(\tau_0^{-2}+v^{-1})^{-1},
\qquad \mu_{\mathrm{post}}=\tau_{\mathrm{post}}^2\bar y/v.
$$

The executable catalog uses the posterior median, rounded to cents:

$$
P_{\mathrm{exec}}=P_{\mathrm{base}}\exp(\mu_{\mathrm{post}}).
$$

For a base price of 100 and one qualified quote of 150, equal prior and observation variances produce 100√1.5 = 122.47. The original 150 quote remains in provenance. See [retail-update tests](../tests/product/realdata/test_a_blocks.py) and [local-quote wiring tests](../tests/product/realdata/test_local_quotes.py).

The current [local-quote manifest](../configs/intake/local_quotes_us_v1.yaml) has no qualified quotes or verified base-price convention records. Consequently, the frozen six-city pack has not triggered this local update and retains base predictions. Directly observed activity prices retain their own source treatment.

A parameter posterior differs from an opportunity distribution. A currently keeps parameter uncertainty but selects a deterministic execution price; its coverage grid has no market-share interpretation. A full posterior predictive opportunity distribution would additionally require a generator q:

$$
q(x\mid D_c)=\int q(x\mid\theta_c)p(\theta_c\mid D_c)\,d\theta_c.
$$

That full-catalog step and annual market sampling from the price posterior are not implemented. Parameter uncertainty, item heterogeneity, and LLM choice variability remain distinct.

See [a_blocks.py](../src/product/realdata/a_blocks.py), [option-family configuration](../configs/intake/life_items_us_v1.yaml), and [mapping design](design/e3b-mapping-and-daily-support.en.md).

<a id="mapping-b"></a>
### B: marginal and conditional distributions

The joint distribution can be factorized as:

$$
p(x_1,\ldots,x_d\mid c)
=p(x_1\mid c)\prod_{j=2}^{d}p(x_j\mid x_1,\ldots,x_{j-1},c).
$$

This identity specifies the needed conditional relationships; marginal distributions alone do not identify the joint distribution. Preserve relationships observed within a record, and declare assumptions when sources cannot be linked.

**Housing: target-cell size × conditional empirical distribution.** Target cell g identifies residence area and bedroom group. Its own survey-weight total is W; a resolved cell R supplies the distribution:

$$
\widehat p(x\mid R(g))
=\frac{\sum_{i\in R(g)}w_i\mathbf1(x_i=x)}{\sum_{i\in R(g)}w_i},
\qquad
\omega(g,x)=W_g\widehat p(x\mid R(g)).
$$

The attribute vector retains rent, bedrooms, rooms, building type, kitchen, and bathroom from the same ACS record. Identical vectors may be merged with provenance and weight retained. Sparse cells fall back through PUMA×bedrooms, commute layer×bedrooms, metropolitan area×bedrooms, then metropolitan stock. Fallback borrows the distribution shape, not the target cell's size.

Use valid same-record gross rent where available. Otherwise add contract rent and the resolved cell's weighted median utilities under the declared rule. Missing donors, top-coded rents, or incomplete kitchen/bath facilities remain recorded but outside the executable pool. The current rule also excludes the metropolitan bedroom-group weighted P10 low-price tail where ordinary-market access is unverified. Preserve excluded records and weight.

**Jobs: occupation employment × wage distribution × joint hours/location distribution.**

$$
\omega_c(o,w,h,\ell)=E_{c,o}\,
\widehat p_c(w\mid o)\,
\widehat p_c(h,\ell\mid g(o)).
$$

E is OEWS employment; g is the broad occupation group. Wages use piecewise-linear interpolation between published P10, P25, P50, P75, and P90 quantiles. Twenty equal-probability intervals span P10–P90, each represented by its midpoint with original-population mass 0.04. The 20% tails and unpublished intervals remain unidentified; wages are not extrapolated.

Hours and work area are jointly estimated from weighted ACS person records. Wages cannot be individually linked to those records, so the combination assumes wages are conditionally independent of hours/location given occupation; the latter distribution is borrowed at broad-group level. Wage support points are scaled by weekly hours relative to 40. This is a modeling assumption, not a consequence of marginal statistics.

An occupation with employment weight 1,000, wage-point mass 0.04, and hours/location mass 0.6 produces candidate weight 24. See [pack tests](../tests/product/test_life_pack.py). Stock housing and employed-person records proxy opportunities; weights are neither live vacancies nor hiring probabilities. Implementation: [mapping_b.py](../src/product/realdata/mapping_b.py), [life_pack.py](../src/product/realdata/life_pack.py), and [commute tests](../tests/product/test_commute_data.py).

<a id="annual-menus"></a>
## Candidate pools and annual menus

The current menu samples 60 housing items and 60 jobs without replacement, using positive candidate weights:

$$
k_j=\log(U_j)/\omega_j,\qquad U_j\in(0,1).
$$

Choose the 60 largest keys. Seeds depend on city, year, environment replicate, and menu configuration, not individual clone ID. Clones share menus in the same environment; querying or revising does not redraw them. Catalogs also contain items needed for starting conditions, minimum living costs, and terminal reference life. Sampling weights are not final inclusion probabilities.

Menus describe exposure to opportunities. Eligibility and affordability still depend on personal state and engine checks. A's coverage grids do not acquire B's empirical weights. Compare menu sizes 30/60/120 through the [mapping design](design/e3b-mapping-and-daily-support.en.md). See [menus.py](../src/product/realdata/menus.py) and [life_pack.py](../src/product/realdata/life_pack.py).

<a id="background-mapping"></a>
## City statistics and life conditions

| Condition | Mapping | Interpretation |
|---|---|---|
| Housing | Bedroom groups → space tiers 1–4; within-cell weighted contract-rent terciles → condition tiers 1–3 | Bedrooms are not area; rent position is a condition proxy |
| Safety | Equal-weight violent/property crime ratios to US rates; index min(10, 6/r); background = index/2 | Aggregate cases and population before calculating rates; weights, inverse relation, and cap are assumptions, not victimization probabilities |
| Environment | Background = 5 × share of valid days with “good” AQI | Annual coverage requirements apply; areas share city background |
| Development | Background = 2 × wage coverage + 2 × training coverage | Coverage across supported occupations proxies available opportunities |
| Leisure | Shared background = 1 | Actual participation is scored separately |

These backgrounds feed [monthly evaluation](METHODS.en.md#monthly-scoring). Adding a city does not rescale earlier cities using new sample extrema. See [configuration](../configs/intake/life_pack_us_v1.yaml) and [safety.py](../src/product/realdata/safety.py).

## Observations and execution

Agents receive permitted prices, conditions, locations, time, personal state, and factual explanations, and select items by ID. They do not invent missing prices or environment values. Hidden scoring parameters and future events stay outside requests.

The engine executes selections and records monthly income, expenses, consumption, travel, and participation. Scores read those records. Content bindings connect packs, rules, plans, and outputs; changed inputs get new identities while old runs retain their original inputs. See [items and occupations](design/e3b-items-data.en.md) and [commuting](design/e3b-commute-data.en.md).

## Calibration priorities

Test A's prior and quote variance; low-price housing filters and menu size; wage/hours/location dependence; commuting and non-work travel approximations; and consumption/background proxies. Calibrate mappings separately from life-score assumptions. Full parameters are in the [register](design/parameter-assumption-register.en.md); validation plans are in [research methods](RESEARCH.en.md#research-questions).

## Missing data

Distinguish unavailable evidence from an unsupported engine concept. Unknown prices are not zero, absent commute relations do not mean zero minutes, and uncovered activities are not assumed available. Gaps can remove an executable item, leave evaluation incomplete, or block the run; preserve the reason.

Read the [coverage report (Chinese)](../data/frozen/intake/us-v1/coverage.md), [gap report](../data/frozen/intake/us-v1/reports/gaps.json), and [pack report](../data/frozen/life/us-v1/pack/report.json). A city list alone does not establish usable coverage.

## Sources and build artifacts

| Artifact | Location |
|---|---|
| Source fields, periods, URLs, licenses | [Manifest](../configs/intake/us_v1.yaml), [source report](../data/frozen/intake/us-v1/reports/sources.json) |
| Frozen identity and coverage | [Manifest](../data/frozen/intake/us-v1/manifest.json), [coverage (Chinese)](../data/frozen/intake/us-v1/coverage.md) |
| Executable catalogs, scores, evaluation, city facts | [Frozen life pack](../data/frozen/life/us-v1/pack/) |
| Pack/input binding | [Product pack configuration](../configs/product/e3b_us_v1_pack.yaml) |
| Mapping assumptions | [Design index](design/README.en.md), [parameter register](design/parameter-assumption-register.en.md) |
| Acquisition, building, trial runs | [Reproduction](REPRODUCING.en.md), [intake design](design/intake-02-real-data.en.md) |

Not all raw downloads or the full intake database are distributed. The repository retains manifests, reports, and the executable frozen pack. Third-party licenses continue to govern their data.

**OpenStreetMap-derived values:** values under `data/frozen/` derived from OpenStreetMap, including city restaurant/venue counts by cuisine, are © OpenStreetMap contributors and available under [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/), not Apache-2.0. Retain attribution; publicly distributed derivative databases follow ODbL. Provenance is in the `osm_overpass` record of `configs/intake/us_v1.yaml`. The interface displays the same attribution in real-pack data sources.

## Add a city

V1.1 will provide a common submission format, mapping checks, and intake process. Today, contribute a defined area such as fares, housing, or wages, with geography, date, units, licensing, and transformations. See [contributing](../CONTRIBUTING.md).

A new city needs reliable sources, aligned measurement conventions, supported engine representations, explicit missingness, and reproducible construction. International comparison additionally requires currency, taxes, qualifications, and opportunity-access differences; exchange-rate conversion alone is insufficient.
