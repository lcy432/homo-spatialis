# E1-6: Evaluation over time and across clones

English · [简体中文](e1-6-evaluation.md)

The evaluator combines experienced life with a limited valuation of the state left at the end. City summaries then distinguish complete evaluations, confirmed life exits and missing simulations.

## Experienced life

For T simulated months, monthly scores S and a declared discount factor δ:

$$
L=\frac{\sum_{m=1}^{T}\delta^{m-1}S_m}
        {\sum_{m=1}^{T}\delta^{m-1}}.
$$

All T months must have valid scores. A partial trajectory retains its known months and reasons for stopping, but their average is not reported as the score of a complete life. The default δ = 1 weights all months equally.

Evaluation verifies the trajectory produced by the annual runner: person, clone, city, years, catalog, tax rules, scoring inputs and successive state handoffs must agree. The evaluator uses the sealed execution records. Editing labels or importing a result JSON does not create a valid evaluation; saved inputs must be replayed through the engine.

## Terminal reference value

From the actual terminal state, the evaluator executes a finite set of predefined twelve-month reference plans. These use the same settlement, resource and scoring rules:

$$
V_p=\frac{\sum_{k=1}^{H}\delta^{k-1}S_{p,k}}
           {\sum_{k=1}^{H}\delta^{k-1}},\qquad
V=\max_{p\in\mathcal P_{\mathrm{complete}}}V_p,\quad H=12.
$$

This is the best complete result within the declared reference set, not an optimization over every possible future life.

Reference templates share roles and selection rules across cities. Each city supplies its own catalog bindings. Current-job and qualified-job branches are paired with basic and more comfortable living arrangements. Real-data housing selection uses the relevant workplace and actual commuting feasibility.

A healthy terminal state needs one branch per template. An ongoing illness adds explicit treatment and no-treatment branches. No new illness is drawn during the reference year, but existing illness continues under the ordinary rules.

| Terminal resource | Treatment |
|---|---|
| Cash | Pays for feasible reference-year living; no separate cash bonus |
| Durable goods | Provide service for their remaining lifetime; no resale bonus |
| Abilities | Can unlock jobs if active at the reference year's opening |
| Incomplete courses | Retained as unfinished; no new or continued training during valuation |
| Health | Ongoing illness and elapsed duration continue |
| Savings goals | No reward for the label or target; only actual resources matter |

The terminal state itself is unchanged. The reference context advances the existing year count and recomputes its opening effort allowance from the final complete action year. It does not reset the person's history.

Each branch is classified as complete, known infeasible, violating a retained hard requirement, or unevaluable. Any unevaluable candidate prevents a valid maximum: silently removing it could discard the best option. The best known branch may be shown as a diagnostic. If no branch is feasible, the value is unavailable, not zero.

Finite templates and a one-year horizon leave some resources unvalued. The report exposes that scope. More cash is not claimed to raise value strictly, or monotonically under every possible rule.

## Combined score

Let W_A be the total actual-period weight and W_F the future-period weight, with future weights continuing from month T:

$$
J=\frac{W_A L+W_F V}{W_A+W_F},\qquad
W_F=\sum_{k=1}^{H}\delta^{T+k-1}.
$$

For three actual years, a twelve-month reference period and δ = 1:

$$
J=\frac{36L+12V}{48}=0.75L+0.25V.
$$

The weights come from months, not a claim that 75% of a person's happiness has been observed. L, V and J are dimensionless model scores, not money or percentages of real happiness.

Score floats are converted through their decimal representation to rational values for aggregation. Rounded display values do not feed decisions.

## City summaries

The roster is frozen before execution:

$$
N=C+E+M.
$$

C counts complete, valid evaluations. E counts confirmed life exits. M includes missing, stopped or otherwise unevaluable simulations. No clone is deleted or replaced because its result is inconvenient.

The city report gives the mean J among C, corresponding L and V means for that same group, and E/N separately. If C = 0, there is no complete-clone mean.

For missing clone i, let K_i be the weighted sum of trusted known monthly scores and W_i their total weight. Let v_i^- = v_i^+ = V_i when its reference value is complete; otherwise use 0 and 1. Its bounds are:

$$
\ell_i=\frac{K_i+W_Fv_i^-}{W_A+W_F},\qquad
u_i=\frac{K_i+(W_A-W_i)+W_Fv_i^+}{W_A+W_F}.
$$

With no trusted records these reduce to [0, 1]. A complete clone contributes [J_i, J_i]. City bounds preserve the known contributions:

$$
J_{\mathrm{lower}}=
\frac{\sum_{i\in C}J_i+\sum_{i\in M}\ell_i}{C+M},
\qquad
J_{\mathrm{upper}}=
\frac{\sum_{i\in C}J_i+\sum_{i\in M}u_i}{C+M}.
$$

These bounds exclude confirmed life exits and show what unresolved simulations could change. They are not confidence intervals. If C + M = 0, they are undefined. Exit rates retain the original N denominator.

A city with a higher complete-clone mean may also have a higher exit rate or more missing simulations. The report keeps those outcomes visible rather than collapsing them into a single ranking.

## Implementation and checks

| Responsibility | Entry point |
|---|---|
| Trajectory verification and L/J | [life_evaluation.py](../../src/econ/life_evaluation.py) |
| Reference execution and V | [life_terminal_value.py](../../src/econ/life_terminal_value.py) |
| City classification and aggregation | [life_aggregation.py](../../src/econ/life_aggregation.py) |
| Contracts | [life_evaluation_contracts.py](../../src/common/life_evaluation_contracts.py) |
| Tests | [Evaluation](../../tests/life/test_evaluation.py), [reference selection](../../tests/life/test_reference_selector.py), [runner](../../tests/life/test_runner.py) |

The [evaluation register](parameter-assumption-register.en.md#evaluation) records the horizon, discounting, template restrictions and missingness policy. Changing these choices changes the meaning of the comparison.
