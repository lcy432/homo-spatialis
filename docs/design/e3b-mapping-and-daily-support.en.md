# E3B: A/B mappings, daily support and consumption

English · [简体中文](e3b-mapping-and-daily-support.md)

The life pack uses two complementary mappings. A starts with comparable option families and updates uncertain parameters with local evidence. B reconstructs opportunities from marginal and conditional distributions. Neither turns source data directly into a personal choice probability.

## B: Housing

A target group g is a residence PUMA and bedroom category. Its own survey weight W_g determines its size. The resolved donor group R(g) supplies a weighted joint empirical distribution:

$$
w_{g,r}=W_g\frac{w_r}{\sum_{u\in R(g)}w_u}.
$$

Each support point preserves jointly observed attributes such as rent, bedrooms and kitchen/bathroom completeness. Equivalent executable records are merged by adding their weights and retaining source identifiers.

The donor hierarchy is PUMA × bedrooms, commute tier × bedrooms, metropolitan area × bedrooms, then metropolitan rental stock. Each group requires at least 30 raw records. A fallback borrows distributional shape, not the donor group's population size.

Valid gross rent is read from the same record. If gross rent is missing for a vacant unit, the model adds the donor group's weighted median utilities to contract rent, recording the imputation. Missing donors, official top codes and incomplete or unknown kitchen/bathroom facilities prevent execution.

The ordinary-market access filter excludes rents strictly below the weighted P10 within metropolitan area and bedroom group. Values equal to P10 remain. Groups with fewer than 30 distinct source records fall back across bedroom groups; insufficient metropolitan support blocks freezing. Original observations remain unchanged.

This filter addresses extremely low paid rents that may reflect subsidies or personal arrangements. It does not prove that excluded homes are fictitious or that retained homes are currently available.

## B: Jobs

The job pool combines occupational employment, wage support and jointly observed work hours and workplace areas:

$$
w_{o,j,h,\ell}
=E_o\,p(w_j\mid o)\,p(h,\ell\mid g(o)).
$$

E_o is occupational employment. The wage quantile function is piecewise linear between OEWS P10, P25, P50, P75 and P90. Twenty midpoint supports cover P10–P90, each with probability mass 0.04. The two tails retain 20% unidentified mass; unpublished segments also remain unidentified.

Hours and workplace area come from the same ACS person record, weighted by PWGTP within a broad occupational group. Sparse groups fall back to all employed people. Top-coded hours retain their missing mass. The job pool does not manufacture remote jobs from work-at-home records.

Because OEWS and ACS cannot be joined person by person, wage is assumed conditionally independent of hours and workplace within the mapped occupation/group. Annual pay is scaled by weekly hours/40. This is a mapping assumption, not an observed wage–hours relationship.

Starting jobs use median pay and the modal hours/workplace support point. People in the same occupation therefore share that starting arrangement. Annual menus subsequently expose alternatives from the full pool.

## Annual menus

For each city, action year and environment repeat, housing and jobs each receive 60 candidates sampled without replacement. The weighted-key algorithm draws u uniformly and orders candidates by:

$$
k_i=\frac{\log u_i}{w_i}.
$$

The largest keys are selected. The seed depends on the menu setting, city, year and environment repeat; it does not depend on clone identity or the entire data-build identity. Re-querying or revising a plan does not redraw the menu.

The current pack has two environment repeats. Diagnostics compare menu sizes 30, 60 and 120 with the full pool and inspect spatial coverage and first-month affordability through the actual engine. Those checks describe exposure and feasibility, not long-run success probabilities.

## A: Common option families and Bayesian price correction

Food, household services, daily support, consumption baskets, appliances, equipment and activities use common structures across cities. Their parameters record units, support, priors, sources and whether local evidence may update them. Coverage-grid counts are not market shares.

For comparable direct quotes, define the log retail correction:

$$
\delta=\log(P_{\mathrm{observed}}/P_{\mathrm{base}}),
\qquad
\delta\sim N(0,\sigma_0^2),\quad \sigma_0=\log(1.5).
$$

After supplier deduplication, let ȳ be the mean log ratio. For n > 1, use v = max(s²/n, log(1.1)²); for one supplier, v = log(1.5)². Treating v as fixed:

$$
\sigma_{\mathrm{post}}^2=
\left(\sigma_0^{-2}+v^{-1}\right)^{-1},\qquad
\mu_{\mathrm{post}}=\sigma_{\mathrm{post}}^2\frac{\bar y}{v},
$$

$$
P_{\mathrm{execution}}=P_{\mathrm{base}}\exp(\mu_{\mathrm{post}}).
$$

The execution price is the implied posterior median. With a base of 100 and one admissible quote of 150, it becomes 100√1.5 ≈ 122.47.

Quotes must match the specification, monetary basis, unit, billing period, taxes/fees and bundle terms. The base-price terms must first be verified for that exact specification. Unknown terms are not guessed.

[local_quotes_us_v1.yaml](../../configs/intake/local_quotes_us_v1.yaml) currently contains no verified base specifications and no quotes. Thus the current six-city pack has no local-quote update. The updater exists and is checked using fixtures; a full posterior predictive option distribution is not implemented.

## Food plans

The common grid covers 21 meals per week: three ingredient baskets, home cooking at 0/3/7/14/21 meals, dining/delivery/mixed alternatives for the remainder, and nine cuisines. Fully home-cooked plans collapse redundant combinations, leaving 327 plans per city.

Ingredient baskets are completed against a common 2,000-kcal daily reference using USDA energy data and a staple-food cost per calorie. This is a budget construction rule, not a personalized nutrition plan.

The dining cost proxy per meal is:

$$
P_{\mathrm{dining}}
=P_{\mathrm{ingredients,week}}/21
+0.25\,w_{\mathrm{cook}}\,2
+2\,RPP_{\mathrm{other\ services}}/100.
$$

Delivery adds 0.2 hours of delivery-driver wages times the same overhead factor. Fees, taxes and tips are not separately modeled. Fresh meals follow the plan's composition; restaurant meals are not automatically fresh.

Cuisine presence uses OSM evidence, with a 20-restaurant threshold. Below it, the common availability assumption is exposed as lacking supply evidence. Consumer expenditure figures are magnitude checks, not menu prices.

## Daily support

| Function | Price basis |
|---|---|
| Personal communication | CE 2024 one-person mobile-service expenditure: 733/12 = 61.08 per month |
| Home connectivity | Weighted regional FCC reference quote for the specified service, converted to 2024 with the relevant CPI |
| Household supplies | One-person CE expenditure, detailed-category allocation and goods RPP |

Broadband falls back to a CE budget proxy only when source availability or sample support is insufficient. Corrupt or fingerprint-mismatched files block the build. The fallback removes unsupported speed/data-cap claims and does not apply another CPI conversion to an already-2024 amount.

A support function counts only while paid for and in use. Duplicate service does not stack. Support changes the convenience score; it does not invent saved hours or another exit condition.

## Personal consumption basket

A common monthly expenditure vector e covers adult clothing, footwear, jewelry, clothing services and personal care. Categories already modeled elsewhere are excluded:

$$
P_c=\sum_j e_j r_{cj},\qquad
\mathrm{cost}_{c}(u)=uP_c,\qquad
s(u)=\min\!\left(0.9,\frac{3u}{3u+3}\right).
$$

u is the quantity actually obtained, from 0.25, 0.5, 1, 1.5, 2, 3 or 4 standard baskets. Goods and services use their respective price factors. Prices affect affordability; scoring reads quantity. The basket is a model abstraction, not a measurement of a person's happiness.

## Reference housing

The current selector first requires known routes to areas representing at least 3/5 of employment weight. Missing routes stay in the denominator. This measures geographic reach, not the share of jobs within an hour.

At valuation, it checks the actual reference job or jobs, available transport functions and any retained commute requirement. Basic housing minimizes rent; the more comfortable one-bedroom middle-quality option minimizes the longest round trip, then rent. Candidates are kept outside action menus.

If earlier candidates are unevaluable, the selector preserves that uncertainty. It does not silently turn missing routes into known absence. Every reference branch records its resolved housing and selection basis.

## Implementation and validation

- [Housing](../../src/product/realdata/housing.py), [life-pack assembly](../../src/product/realdata/life_pack.py) and [reference housing](../../src/product/realdata/reference_housing.py).
- [Items](../../src/product/realdata/life_items.py), [local quote tests](../../tests/product/realdata/test_local_quotes.py) and [real-data tests](../../tests/product/realdata/).
- [Parameter register](parameter-assumption-register.en.md#mapping) and [Data guide](../DATA.en.md#mapping-methods).

Full reports are in [the frozen pack](../../data/frozen/life/us-v1/pack/). They record excluded mass, imputation, source gaps and menu diagnostics. Current formulas and limits above supersede the early three-point housing and job examples retained in the Chinese design history.
