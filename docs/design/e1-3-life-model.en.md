# E1-3: Living conditions and satisfaction

English · [简体中文](e1-3-life-model.md)

This guide describes the current scoring model. The Chinese edition also retains earlier examples and development notes. Start with [Model and evaluation](../METHODS.en.md); current parameters are in [scoring_product.yaml](../../configs/life/scoring_product.yaml).

## What is being evaluated?

The planner chooses a way of life; the engine records what the person actually receives. The scorer evaluates those conditions using the person's confirmed preferences. A more expensive apartment does not automatically provide a better experience. Its price matters through the budget and the other choices it displaces.

Preferences, initial resources and abilities belong to the person. Choices depend on the city and the history of the simulation:

$$
a_{icy}=\pi(\theta_i,\mathcal O_{cy},x_{icy}).
$$

Moving from a large apartment to a small one need not mean that the person has stopped valuing space. The comparison holds the person fixed while allowing their choices to differ.

## From conditions to component scores

Most components use a capped saturation function:

$$
z_j=b_j+\Delta_j,\qquad
s_j=\min\!\left(c_j,\frac{z_j}{z_j+\tau_j}\right).
$$

Here, b is the background condition, Δ is the improvement actually obtained, τ is a positive scale shared across cities, and c is the ceiling. Before the ceiling applies, z = τ gives a score of 0.5. The marginal gain is τ/(z + τ)²: the same improvement matters less at a higher starting level.

Sources can be added, compared by their maximum contribution, or counted once when a function is present. The choice depends on what the sources represent. Two substitutes for the same need do not automatically provide twice the benefit. Quantities with different units cannot be added without an explicit conversion.

The current six dimensions are material life, safety, development, leisure, environment and transport. Material life contains four components:

| Component | Actual conditions used |
|---|---|
| Housing | Space adequacy and estimated quality within the same area and bedroom category |
| Food | Fresh meals and meals matching confirmed cuisine preferences |
| Daily convenience | Household work avoided and three basic support functions actually obtained |
| Personal consumption | Units of a standard consumption basket actually received |

The current housing mapping uses space and quality levels; floor area examples in early design notes explain the older mechanism. Rent, wages, purchases and cash balances are not direct satisfaction inputs.

For food:

$$
z_{\mathrm{food}}=F+0.5T,\qquad
s_{\mathrm{food}}=\min\!\left(0.9,\frac{z_{\mathrm{food}}}{z_{\mathrm{food}}+7}\right).
$$

F counts fresh meals per week; T counts meals whose primary cuisine matches a confirmed preference. The two counts describe different attributes of the same meals.

For daily convenience, q is the monthly household work avoided, as recorded by the resource ledger, and n is the number of support functions actually obtained:

$$
s_{\mathrm{convenience}}
=0.5\min\!\left(0.9,\frac{q}{q+8}\right)
+0.5\left(0.9\frac{n}{3}\right).
$$

The three functions are personal communication, home connectivity and household supplies. Duplicate provision does not count twice. For personal consumption, x standard baskets give min(0.9, 3x/(3x + 3)). These scales and weights are exploratory settings, not estimates of psychological responses.

## Preferences and health

Let m be the normalized weights of the four material components and w the normalized weights of the six dimensions:

$$
S_{\mathrm{material},im}=\sum_j m_{ij}s_{ijm},\qquad
B_{im}=\sum_{k=1}^{6}w_{ik}S_{ikm},
$$

$$
S_{im}=(1-\gamma)B_{im}+\gamma H_{im},
\qquad \gamma=0.2.
$$

H is 1 for healthy, 0.5 for minor illness and 0 for major illness, using the month's opening health state. Health has a common weight. The user's health-risk coping answer informs planning; it does not change the illness draw or the direct health penalty.

Current product inputs are independent ratings from 0 to 100, normalized only after the required answers are complete and their sum is positive. Unanswered questions remain unanswered; an all-zero answer is not replaced with equal weights. See [User input](e3a-product-input.en.md).

## Participation, growth and transport

Leisure improvements use actual participation hours. Confirmed interest matches increase the contribution of those hours by the configured match bonus; buying equipment alone does not count as participation. Preparation and travel consume resources but are not leisure participation hours.

Development adds actual increments in ability progress. Overlapping courses granting the same ability use the highest progress, rather than adding duplicate progress. See [Career, savings and illness](e1-5-career-savings-risk.en.md).

Transport uses:

$$
T_{\mathrm{eq}}=\left[\rho+(1-\rho)\frac{D}{D_{\mathrm{ref}}}\right]T,
\qquad s_{\mathrm{transport}}=\frac{\tau_T}{\tau_T+T_{\mathrm{eq}}}.
$$

D is actual workplace attendance; T is the relevant round-trip time. The current common settings are ρ = 0.25, D_ref = 20 and τ_T = 60 minutes. The residual term represents travel conditions even without workplace attendance. It is an assumption, not a measurement of non-work journeys. Actual commuting time is accounted for separately.

## Records, missing data and coverage

Scoring reads executed months, holding-service records and actual participation. A prepaid service has one authoritative service record; it is not counted again at payment. Durable goods contribute while in service, not once at purchase and again during use.

Known non-consumption contributes no improvement. An unknown attribute is different: if it affects a component with positive weight, the total is incomplete. The scorer does not replace the value with zero or renormalize the remaining weights. Unknown zero-weight components need not block the total.

Coverage reports describe which preferences the model can express. Coverage alone does not certify a complete score. Legacy hard requirements are reported separately; the current questionnaire no longer asks new users for them.

The annual mean requires twelve complete monthly scores. Cross-year evaluation and city aggregation belong to [E1-6](e1-6-evaluation.en.md).

## Implementation and checks

| Responsibility | Entry point |
|---|---|
| Component and result types | [life_scoring_contracts.py](../../src/common/life_scoring_contracts.py) |
| Scoring configuration | [life_scoring_config.py](../../src/ledger/life_scoring_config.py) |
| Monthly scoring | [life_scoring.py](../../src/econ/life_scoring.py) |
| Preference coverage | [life_coverage.py](../../src/econ/life_coverage.py) |
| Behavioral checks | [Scoring tests](../../tests/life/test_scoring.py), [convenience tests](../../tests/life/test_daily_convenience.py), [extension tests](../../tests/life/test_life_extension.py) |

The scorer does not change the accounts or read the planner's justification. Score parameters and internal preference truth do not enter the planner's observation. Changes to these rules must also update the [parameter and assumption register](parameter-assumption-register.en.md#scoring).
