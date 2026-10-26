# Parameter and assumption register

English · [简体中文](parameter-assumption-register.md)

This English register covers the current mechanisms and their stable record families. It is organized for reading alongside the technical guides. The [detailed register (Chinese)](parameter-assumption-register.md) retains individual historical rows, superseded examples and development notes; this edition is not a line-by-line translation of that history.

A listed value may be executable without being empirically calibrated. The configuration and input snapshot used by a run determine its actual values.

## How to classify a record

| Code | Meaning | Examples |
|---|---|---|
| W | World rule | Resource constraints, learning, illness and household substitution |
| M | Data mapping | Survey records to rent supports; public indicators to backgrounds |
| P | Personal input | Confirmed preferences, resources, qualifications |
| B | Information and cognition | What the planner receives, memory and prompt representation |
| E | Evaluation | Satisfaction scales, reference horizon and missingness treatment |
| X | Experimental design | Pairing, menus, repeats and comparison arms |
| N | Numerical or engineering control | Precision, fingerprints, limits and solver settings |

A record can have several roles. A derived quantity is not automatically another free parameter. Confirmed preferences, unit conversions, accounting conservation and information isolation are not optimization targets.

For each changed record, preserve its ID, meaning, units/domain, value or formula, status, configuration/code location, source, causal role, visibility to the planner, omissions, affected groups and validation method.

<a id="scoring"></a>
## Living-condition scores

| IDs | Current rule or interpretation | What needs examination |
|---|---|---|
| SC-01–06 | Capped saturation; positive τ; nonnegative backgrounds/contributions; declared source-combination rule | Scale, curvature, ceiling and whether sources are additive or substitutes |
| SC-07–08, SC-13, SC-15 | Personal weights and actual service records; unknown positive-weight components block the total; money is not a direct score | Preference coverage and record completeness |
| SC-09–14 | Landscape/transport mappings, retained hard requirements and housing-linked conditions | Earlier protocol restrictions are historical; current transport supports multiple work arrangements |
| SC-16–18 | Household work avoided from the resource ledger; complete substitutes use the minimum residual | Baseline chores, residual work and whether the model represents the person's routine |
| SC-19–21 | Evolution to four material components and six life dimensions | Earlier three-component/percentage inputs are not the current questionnaire |
| SC-22 | Food: fresh-meal coefficient 1, cuisine-match coefficient 0.5, τ = 7, ceiling 0.9 | Whether meal counts and cuisine matches adequately represent food preferences |
| SC-23 | Personal consumption: z = 3u, τ = 3, ceiling 0.9 | Basket composition and the assumed quantity–satisfaction relationship |
| SC-24 | Leisure participation coefficient 1 per hour, confirmed-match bonus 0.5, τ = 12 | Paid/free activity equivalence, category granularity and omitted experiences |
| SC-25 | Synthetic opportunities used to exercise the contracts | Fixtures are not observations of a population |
| SC-26 | Housing: space adequacy plus estimated within-group quality, τ = 3 | Bedrooms as a space proxy and rent-derived quality assumptions |
| SC-27–28 | Actual residence area selects background; actual commute records determine travel inputs | Geographic resolution, route availability and non-work travel |

The central function is:

$$
s=\min\!\left(c,\frac{b+\eta x}{b+\eta x+\tau}\right).
$$

Current component formulas, including the separate common health term, are in [Living conditions](e1-3-life-model.en.md). Values are defined in [scoring_product.yaml](../../configs/life/scoring_product.yaml), with pack-specific conditions in the frozen pack.

<a id="accounting"></a>
## Settlement and money

| IDs | Rule | Interpretation |
|---|---|---|
| AC-01–02 | Annual decisions, monthly execution, default three action years | Decision frequency and horizon are model choices |
| AC-03–07 | Explicit initial resources; housing/food minimum; declared fallback; periodic versus one-time failure; no borrowing | Cash constraints are not inferred preferences |
| AC-08–09, AC-13 | Service months, lifetime, activation, prepaid coverage and course participation | Payment, ownership, service and completion remain separate |
| AC-10, AC-14 | Synthetic taxes in fixtures; checked 2024 tax rules in the US pack | Statutory tables are source inputs, not fit parameters; tax scope is limited |
| AC-11–12 | Confirmed life exits; integer minor-unit accounting and exact conservation | Exit is different from missing computation |
| AC-15 | Equipment failure and declared replacement | Annual p converts to monthly hazard 1 − (1 − p)^(1/12); a no-failure setting is an assumption |

Cash follows opening balance plus actual income minus tax and actual payments. Rounding allocates annual amounts without losing cents. Neither this identity nor a currency conversion constant should be fitted to improve results.

See [Model and evaluation](../METHODS.en.md#settlement) and [settlement code](../../src/econ/life_settlement.py).

<a id="resources"></a>
## Time, effort and feedback

| IDs | Current rule | Sensitivity or limitation |
|---|---|---|
| RE-01–02 | Annual net hours/12; no monthly carryover | Does not schedule shifts or care within a day |
| RE-03–05 | Base monthly effort allowance and activity-specific burdens/hours | The effort scale is conventional; wage evidence does not validate burden |
| RE-06–07 | Pay binds to work arrangement; each job's attendance creates its own commute | No consolidation of trips between jobs |
| RE-08 | Household baseline and minimum residual across complete substitutes | No general model of complementary household stages |
| RE-09–10 | Relative work burden is a derived ratio; exact resource comparison | Known lower bounds are not complete observations |
| FB-01–05 | α in [0, 0.10], current 0.10; complete previous-year score; floor to 0.01; content-bound handoff | Bounded positive feedback from a fixed base, not compounding or a physiological law |

$$
Q_{y+1}=\operatorname{floor}_{0.01}
\left[Q_{\mathrm{base}}(1+\alpha\bar S_y)\right].
$$

The current synthetic base is 250 burden points. Missing annual scores block feedback. See [Time and effort](e1-4-time-energy.en.md).

<a id="mapping"></a>
## Data mappings

MP-01–09 cover monetary bases, unit conversions, background indices, housing/geography, provenance, comparison-group consistency and coverage. Current real-data extensions are registered in the PI families below.

| IDs | Mapping | Main assumption or boundary |
|---|---|---|
| PI-17–19, PI-43 | Weighted housing support and target groups | Recently moved/vacant units proxy a new rental market; sparse groups borrow shape |
| PI-20, PI-44 | Employment × wage support × joint hours/workplace distribution | Conditional independence across unlinked sources; P10–P90 leaves 20% unidentified tails |
| PI-21–22, PI-24–25 | Ingredient, activity, geographic and appliance source handling | True records, derived estimates and geographic applicability remain distinct |
| PI-23, PI-48 | Weighted sampling without replacement; 60 homes/jobs; two environment repeats | Exposure is not supply, transactions or hiring |
| PI-28–29 | Complete 21-meal plans and dining/delivery cost proxies | Common calorie reference, cooking time and labor overhead |
| PI-30–32 | Household labor, equipment and model activity costs | Assumed service hours, lifetimes, installation and venue/material costs |
| PI-33 | Calendar-based Chicago public activities | Evidence retained; unsupported restrictions prevent execution |
| PI-34–36 | Occupation scope, confirmed qualifications, starting jobs and training | Typical entry education treated as a requirement; no invented license |
| PI-37–42 | Areas, commute medians, fare evidence, fuel and car access | Pooled travel times, 25 mph speed proxy, national non-fuel car fee |
| PI-45, PI-49 | Safety, air quality, development and leisure backgrounds | Indicator-to-score scaling and geographic/reporting coverage |
| PI-46 | Common treatment offers | Current prices are assumptions, not local medical price observations |
| PI-47 | Reference housing and life templates | Known employment reach ≥ 3/5, then actual-job commute checks |
| PI-50 | Content-bound assembly | Integrity checks establish provenance, not empirical validity |
| PI-51, PI-58 | Bayesian retail correction and verified comparable quote terms | No current admissible local quotes; no full posterior predictive catalog |
| PI-52–53 | Free participation paths, dominance diagnostics and mock repair rules | Mock behavior is a test condition |
| PI-54–55 | Daily support and personal consumption basket | Expenditure-based proxies and regional broadband references |
| PI-56 | Reversible request compression | Same facts and choices must survive the representation |
| PI-57 | Ordinary-market rent filter below weighted P10 | Access proxy, not proof of listings or false observations |
| PI-59 | Menu/first-month feasibility diagnostics | Fixed diagnostic person and resources; not a population success rate |

The two mappings have different mathematical roles:

$$
\text{A: }p(\delta\mid D)\propto p(D\mid\delta)p(\delta),
\qquad
\text{B: }p(x,y)=p(x)p(y\mid x).
$$

See [A/B mappings](e3b-mapping-and-daily-support.en.md) for priors, likelihood variance, joint supports, sampling and reference selection; [commuting](e3b-commute-data.en.md) for route formulas; [items](e3b-items-data.en.md) for opportunity construction.

<a id="information"></a>
## Information conditions

IN-01–08 distinguish actual world risk p, the information I supplied to the planner and its resulting belief q. They are not the same quantity.

Visible facts include public prices, hours, eligibility, current state and permitted past outcomes. Internal score rules, preference truth and future events remain hidden. The default condition does not disclose the simulator's illness–workload risk relationship; public activity costs remain visible.

Hiding a field does not remove the language model's prior knowledge or prevent inference. Proposed risk-belief interventions are future experimental conditions, not existing calibrated cognitive parameters. Changing information while holding world risk fixed is a different experiment from changing world risk.

<a id="long-term"></a>
## Career, savings and health

| IDs | Current mechanism | Boundary |
|---|---|---|
| CA-01–05 | Scoped abilities, paid tuition, actual participation, next-year activation and deterministic qualified access | No skill decay, labor competition or automatic degree from a job title |
| CA-06 | Growth from increments in ability progress; maximum over courses sharing an ability | Splitting an ability vocabulary can change the reward |
| SV-01–02, SV-04 | One recorded active savings goal; shared cash ledger; linked full-payment fulfillment | No earmarked funds, interest or automatic purchase |
| SV-03 | Separate reassurance-from-savings reward excluded | A goal's label does not add satisfaction |
| HK-01–02 | Age × previous complete month's relative work burden; one illness at a time | Exploratory risk table and short history window |
| HK-03–05 | Recovery costs, treatment, leave and predeclared contingencies | Earlier committed months survive event-driven stops |
| HK-06–09 | Paired events, common direct health term and planning-only coping input | Health scores and risk mechanisms are not individual medical estimates |

The direct health weight is γ = 0.2 with values 1/0.5/0 for healthy/minor/major. See [Career, savings and illness](e1-5-career-savings-risk.en.md) and the health rules in the run snapshot.

<a id="evaluation"></a>
## Evaluation

EV-01–19 cover discounting, the reference horizon, verified trajectories, terminal resources, reference branches, city aggregation and valuation scope.

| Choice | Current meaning |
|---|---|
| Monthly discount factor | δ = 1 by default; a reporting parameter, not inferred patience |
| Terminal horizon | H = 12 months |
| Reference value | Best complete, feasible branch in a finite declared set |
| Reference events | No new illness; existing illness continues |
| Reference learning | No new or continued courses |
| Remaining assets | No second terminal reward after reference life |
| Missing results | Retain known monthly contributions, bound unknown scores in [0, 1] |
| City mean | Complete evaluations only; exits reported against the original roster |
| Equipment scope | Disclose uses omitted by reference templates |

For T = 36 and δ = 1, J = (36L + 12V)/48. These weights and the reference set influence how the model values late learning or saving. See [Evaluation](e1-6-evaluation.en.md) for the full formulas and denominators.

<a id="agent-loop"></a>
## Planner and execution conditions

AG-01–25 record the planning interface, city-fact alignment, memory, prompts, revision allowance, failure scope, effective model settings, budget controls, archives and annual menus.

The default annual loop has one initial attempt and at most two revisions. Network retries do not create more plan revisions. Recorded responses are reused during replay; unknown send status is not automatically retried.

Model roles and context/output limits are versioned engineering choices. Read [models.yaml](../../configs/models.yaml) and the actual run configuration rather than copying an earlier pilot's model name or price. A mock rule is a fixture, not evidence of an LLM's decision quality. See [Agent loop](e1-7-agent-loop.en.md).

<a id="product-input"></a>
## Product input and display

PI-01–16 cover the original input/confirmation design; PI-26–27 add interests and life-item compilation. Later current rules include:

| IDs | Rule |
|---|---|
| PI-60 | Defaults, allowed ranges and effective run settings remain separate; each run has its own budget ledger |
| PI-61 | Versioned geographic anchors and score display scales do not alter business values |
| PI-62 | Structured occupation, education, certificate and experience inputs |
| PI-63 | Independent 0–100 ratings normalized exactly after complete, positive-sum answers |

Current confirmation does not migrate old questionnaires or infer ratings from old percentages. See [User input](e3a-product-input.en.md) and [Shared contract](e3-e5-product-contract.en.md).

<a id="legacy"></a>
## Static research benchmark

| Family | Role |
|---|---|
| LG-01–10 | Cobb–Douglas utility, fixed personal expenditure shares, price indices and money-equivalent conventions |
| WG-01–12 | Synthetic world distributions, correlations, tax scenarios and persona populations |
| EX-01–09 | Noise, recovery specifications, experimental arms, sampling and predeclared gates |
| NM-01–04 | Integration, optimization, precision, numerical warnings and engineering limits |

These belong to the controlled research benchmark. Fixed expenditure shares there are not mandatory spending shares for the dynamic life planner. Fitted posteriors, covariance ratios and measurement floors are outputs of the experiment, not world parameters.

Full English formulas and validation criteria are in [Research methods](../RESEARCH.en.md). The [detailed legacy rows (Chinese)](parameter-assumption-register.md#legacy) retain historical example values.

<a id="v2"></a>
## Future mechanisms

V2-01–12 register possible richer consumption attributes, substitution/complementarity, hobbies, equipment lifecycles, international classification bridges, family/location ties, information access, daily scheduling, interviews, health/skill dynamics, adaptation and neighborhood conditions.

They are design candidates, not implemented configuration switches. The public release sequence is maintained in [the roadmap](../../PLAN.en.md): V1.1 city contributions, V2 richer daily-life mechanisms and mappings, V3 interview-based input. Older register labels do not override that sequence.

<a id="v3"></a>
## Calibration and identification

Calibration begins with observations that can distinguish rules. Time-use evidence can constrain household hours; comparable quotes constrain costs; external decisions or life assessments may constrain parts of a score mapping. Recovering parameters from our own generated scores checks estimation, not realism.

Some parameters cannot be identified separately. For any positive scale a:

$$
\frac{a(b+\eta x)}{a(b+\eta x)+a\tau}
=\frac{b+\eta x}{b+\eta x+\tau}.
$$

Likewise, scaling both the effort allowance and every burden coefficient can leave feasibility unchanged. Units and necessary anchors must be fixed before interpreting fitted coefficients.

A calibration study should specify adjustable parameters, admissible ranges, target observations, fixed conditions, the training/validation split, a computational budget and a stopping rule. Heuristic search, hyperparameter search and learned mappings can be compared where suitable data are available. Fitting the calibration targets does not by itself establish that the rules are realistic or uniquely identified.

Calibrate world rules separately from testing the planner. Keep evaluation criteria and user-confirmed preferences distinct from fit parameters. Check held-out cities, periods and groups, and report where improvement for one group worsens another. Rescoring a fixed trajectory differs from changing feedback and rerunning life.

## Maintenance

When changing a mechanism, record which IDs changed, which values remain assumed or unresolved, which proposals remain unimplemented, and whether observations, scores, state transitions or comparability changed. Update the paired guides and link meaningful checks. Do not use a documentation cleanup to alter production mathematics.
