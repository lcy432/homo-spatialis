# E3B: Assembling and freezing the six-city life pack

English · [简体中文](e3b-life-pack.md)

The life pack combines standardized source records, commuting data, daily-life opportunities, tax rules and declared model assumptions into inputs the life engine can execute.

## Supported comparison

The pack covers New York, Los Angeles, San Francisco, Chicago, Houston and Seattle under their 2023 metropolitan definitions. San Francisco excludes San Jose. Tax support restricts New York residences to the five boroughs and workplaces to New York State, and Chicago to Illinois.

The supported tax scenario is a single person with wage income and the standard deduction. The occupation catalog covers 40 occupations. Households, non-wage income, tax credits and cross-border comparisons require further work.

The pack is labeled `real_data_frozen` because it uses frozen public evidence. Illness rules, satisfaction scales and several opportunity mappings remain exploratory assumptions.

## Inputs and outputs

Assembly reads the intake database, raw evidence bound to that database, frozen commuting and item data, and checked tax/safety evidence.

| Output | Contents |
|---|---|
| `life_input.json` | Protocol, city catalogs, areas, routes, jobs, annual menus, minimum living arrangements, treatment and taxes |
| `scoring.json` | Shared scoring specification and city conditions |
| `evaluation.json` | Terminal reference templates and city-specific selectors |
| `city_facts.json` | Planner-visible background facts and coverage descriptions |
| `report.json` | Counts, exclusions, mapping diagnostics and build inputs |

The assembler recomputes input content identities. A changed price under an unchanged manifest is rejected. Valid source updates produce a new build identity. Identical source content and rules reproduce the same frozen output; menu seeds are governed separately by the sampling policy.

## Construction and use

After source downloads and the intermediate commuting/item builds, the main offline steps are:

~~~bash
uv run python scripts/life_pack_us.py check-tax
uv run python scripts/life_pack_us.py check-safety
uv run python scripts/life_pack_us.py build
~~~

The build writes [the frozen pack](../../data/frozen/life/us-v1/pack/) and [its product configuration](../../configs/product/e3b_us_v1_pack.yaml). The local intake database and raw caches are required; a fresh checkout alone does not contain them.

An offline product run uses:

~~~bash
uv run python scripts/run_product_full.py --out runs/my-six-city-run --mock --pack configs/product/e3b_us_v1_pack.yaml --abilities edu_at_least_hs
~~~

See [Reproducing runs](../REPRODUCING.en.md) for setup and output interpretation.

## Housing, jobs and everyday choices

Housing uses the full weighted empirical support pool after the ordinary-market access filter. Jobs combine wage supports with the observed hours/workplace joint distribution. Annual menus sample 60 housing and 60 job options for each city, year and environment repeat.

Food covers complete weekly meal plans. Daily support and personal consumption are explicit services rather than hidden background expenditure. The current mappings are documented in [A/B mappings](e3b-mapping-and-daily-support.en.md); earlier three-point menus and separate massage rewards in the Chinese record are superseded.

## Background conditions

Safety combines metropolitan violent- and property-crime rates relative to a fixed national reference:

$$
r=0.5\frac{v_c}{v_{\mathrm{US}}}
 +0.5\frac{p_c}{p_{\mathrm{US}}},
\qquad I_{\mathrm{safety}}=\min(10,6/r),
\qquad b_{\mathrm{safety}}=I_{\mathrm{safety}}/2.
$$

Rates are computed from summed counts and populations, not averages of county rates. The mapping is a common scale assumption. It does not simulate victimization or personal injury.

New York uses DCJS county totals for the state's part of the metropolitan area; the other cities use FBI metropolitan tables with their stated reporting or estimation basis. New York's missing New Jersey/Pennsylvania coverage and Chicago's included Indiana coverage are visible even though simulation residence support differs.

Air-quality background uses the share of valid observed days in the good-AQI category. Development uses wage and training coverage proxies. The common leisure background is 1; actual participation adds to it. Source coverage is not itself proof of a city's quality of life.

Planner-visible facts expose meaning, direction and coverage. They omit internal score coefficients and reference anchors. Alignment checks ensure that visible facts and scoring inputs describe the same city.

## Reference life and remaining gaps

Reference housing first passes an employment-coverage check, then is resolved against the reference branch's actual jobs and transport access. The [selector](e3b-mapping-and-daily-support.en.md#reference-housing) avoids binding every clone to one home chosen without its terminal job.

Known gaps include San Francisco transit fares, restricted training coverage, museum prices, activity calendars/age restrictions and some crime-report geography. Model activities are distinct from directly observed offers. Chicago's dated public programs remain evidence rather than executable unrestricted activities.

Mock runs, replay and sensitivity checks validate execution under these rules. They do not measure real users' outcomes or establish a ranking of cities.

## Implementation and verification

- [Life-pack assembly](../../src/product/realdata/life_pack.py).
- [Commuting](e3b-commute-data.en.md), [items and careers](e3b-items-data.en.md), [intake](intake-02-real-data.en.md).
- [Engine integration tests](../../tests/life/test_e3b_engine.py), [real-data tests](../../tests/product/realdata/).
- [Parameter register](parameter-assumption-register.en.md#mapping) and [current status](../INDEX.en.md#status).
