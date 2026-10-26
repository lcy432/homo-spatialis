# E1-4: Time, effort and annual feedback

English · [简体中文](e1-4-time-energy.md)

A plan must fit the person's monthly time and effort limits as well as their cash balance. This guide describes the current resource rules; the Chinese edition retains the original worked examples and implementation history.

## Monthly resource constraints

For activities a actually executed in month m:

$$
T_m=\sum_a h_{am}\leq H_{\mathrm{annual}}/12,\qquad
E_m=\sum_a \lambda_a h_{am}\leq Q_y.
$$

h is time in hours, λ is burden per hour, and Q is that year's monthly effort allowance. Time and effort are distinct constraints. Money cannot purchase an exemption from either. Unused capacity expires at month end.

The annual time budget is already net of basic needs such as sleep. The model does not subtract those needs again. Work, commuting, cooking, household work, learning and active participation consume resources. Passive ownership does not create activity hours.

Unknown hours or burden coefficients are data problems, not zero-resource activities. Public wage evidence does not validate an assumed effort coefficient.

## Work and commuting

A job binds pay, hours, workplace attendance and burden. The planner cannot retain its wage while rewriting its hours to zero.

For each executed job:

$$
h_{\mathrm{commute},m}
=\sum_j \frac{D_{jm}T_{jm}}{60}.
$$

D is attendance count and T is round-trip minutes from the actual home to that job's workplace. Two jobs produce two sets of work and commuting requirements. Attendance counts can exceed the number of calendar days because they count trips, not distinct days. The model does not combine trips between jobs.

Remote jobs with zero attendance have zero work commuting. The real-data pack selects among available routes using the [commuting rules](e3b-commute-data.en.md). Housing fallback changes the route used; a cheaper home's rent cannot be combined with the original home's commute.

## Cooking and household work

Cooking and dining time come from the food plan actually executed. There is no second generic cooking charge.

Each household task has a baseline monthly requirement. Equipment or services can provide a complete substitute with a stated residual amount of the person's time:

$$
h_{\mathrm{task},m}
=\min\!\left(h_{\mathrm{baseline}},
h_{\mathrm{residual},1},\ldots,h_{\mathrm{residual},r}\right).
$$

For example, a 12-hour task, a machine leaving 3 hours and a service leaving 1 hour result in 1 hour of personal work. Subtracting both savings would produce a meaningless negative requirement.

A substitute works only while its service is actually available. This rule models complete alternatives; complementary stages need their own task or combined specification. Avoided household work also feeds the current convenience score, but resource accounting and scoring remain separate calculations.

## Execution and failure

Resource checks follow the same executed choices as the money ledger, including fallback housing, fallback food and failed purchases. A structurally valid plan that exceeds a monthly limit is infeasible. The engine reports the first offending month, the limit, actual use and major contributors; it does not silently delete activities.

Before the year starts, feasibility is checked without new illness events. A rejected plan produces no committed transactions or next-year state. Once actual events occur, the engine follows the declared contingency plan and preserves earlier committed months. It does not return to January to ask for a better plan.

An unaffordable minimum living bundle is a life exit. In that month, known work and income still matter: earning wages cannot erase the labor that earned them. If only a lower bound on resource use is known, the record says so. A lower bound below the limit does not certify a complete month.

## Satisfaction and the following year's allowance

With feedback enabled:

$$
Q_{y+1}
=\operatorname{floor}_{0.01}\!
\left[Q_{\mathrm{base}}\left(1+\alpha\bar S_y\right)\right].
$$

The allowance is recomputed from the base, not multiplied into the previous year's allowance. It is therefore bounded by the configured score range rather than compounding indefinitely. The current synthetic settings use Q_base = 250 and α = 0.10.

The annual score must be complete. Missing scores prevent the transition to the next year; they are not replaced with zero. Disabling feedback, or setting α to zero, gives the base allowance. Unused time and effort do not themselves replenish it.

The handoff records the scoring content identity. Changing a parameter while retaining a configuration name does not permit an allowance generated under the old rule to continue under the new one. Comparable interventions restart from the same initial state.

This feedback is a simulation assumption. It is not an empirically fitted relationship between satisfaction and human energy.

## Implementation and checks

| Responsibility | Entry point |
|---|---|
| Monthly resources and household substitutions | [life_resources.py](../../src/econ/life_resources.py) |
| Monthly settlement | [life_settlement.py](../../src/econ/life_settlement.py) |
| Annual allowance | [life_feedback.py](../../src/econ/life_feedback.py) |
| Annual execution and handoff | [life_year.py](../../src/econ/life_year.py) |
| Verification | [Resource tests](../../tests/life/test_resources.py), [feedback tests](../../tests/life/test_feedback.py), [health tests](../../tests/life/test_health.py) |

See the register sections on [resources](parameter-assumption-register.en.md#resources) and [information](parameter-assumption-register.en.md#information). Daily scheduling, fatigue within a day and a physiological recovery model remain outside the current rules.
