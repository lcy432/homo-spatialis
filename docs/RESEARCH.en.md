# Research questions and measurement

English · [简体中文](RESEARCH.md)

[Documentation index](INDEX.en.md) · [Life-simulation methods](METHODS.en.md) · [References](REFERENCES.en.md)

This guide covers preference understanding, calibration, data mapping, and measurement. The static utility model creates experiments with known answers; the three-year life simulation uses its own execution and evaluation rules.

<a id="research-questions"></a>
## Research questions

**Whom does the model misunderstand, and who bears the cost?** Average accuracy can hide neglected preferences and concentrated mistakes. We study which people lose from a mistaken city choice, using known-preference synthetic personas and cost-of-living baselines.

**Can calibration make the simulated world more realistic?** Rules encode assumptions about wages, consumption, commuting, skill growth, and life conditions. We want to estimate parameters and relationships from observations and test whether the simulation better explains different groups' lives and decisions. Candidate approaches include heuristic search, parameter/hyperparameter optimization, and machine or deep learning for specific mechanisms.

Specify observable targets and error measures first, then validate on people, cities, or periods not used for calibration. Report aggregate and subgroup errors. Observable relationships, confirmed preferences, and normative evaluation criteria require separate treatment. Sensitivity analysis identifies consequential rules. Systematic calibration remains future work; existing assumptions are in the [register](design/parameter-assumption-register.en.md).

**How can real data support simulated decisions?** Statistics must become available homes, jobs, and activities. Test whether transformations retain important attribute relationships, represent accessible opportunities, and propagate conversion errors. See [intake](design/intake-02-real-data.en.md) and the [six-city pack](design/e3b-life-pack.en.md).

Implementation status and plans belong in the [index](INDEX.en.md) and [roadmap](../PLAN.en.md); this page defines methods, not current experimental findings.

## Static experiments and dynamic simulation

| | Static understanding experiment | Three-year life task |
|---|---|---|
| Observation | Trade-offs in fixed city-choice questions | Trajectories from work, housing, and consumption choices |
| Evaluation | Injected truth preferences and a static economic core | Shared constraints, settlement, and multi-period evaluation |
| Purpose | Locate preference misunderstanding and recovery bias | Compare personal simulated lives and planning consequences |
| Reference | Optimal city and simple baselines under the same truth | Reference policies with the same information, actions, and terminal rules |

Static research has seven preference dimensions; product input has six life dimensions and separate health information. They are not interchangeable, and a static argmax is not an already-solved three-year optimum. See [life scoring](design/e1-3-life-model.en.md) and [evaluation](design/e1-6-evaluation.en.md).

Separate input instability, model variability under fixed input, and environment/data uncertainty. More clones in a fixed environment primarily measure the second.

<a id="economics"></a>
## Static economic benchmark

### Personal prices and utility

Individual expenditure shares over rent, goods, and other services define the price index:

$$
\ln P_{i,c}=\sum_{j\in\{\mathrm{rent,goods,other}\}}s_{i,j}\ln p_{c,j},
\qquad \sum_j s_{i,j}=1.
$$

The same person's basket stays fixed across cities rather than changing with candidate-city income. RPP is normalized from 100 to 1. Within-area CPI is not a cross-city price level. Variety adjustment is an extension; `NullVarietyAdjustment` currently returns zero.

$$
U=C^\alpha H^\beta\prod_{k=2}^{7}A_k^{\theta_k},
\qquad \alpha+\beta+\sum_{k=2}^{7}\theta_k=1.
$$

C is nonhousing consumption, H housing services, and A transformed amenities where higher is better. Unit substitution elasticity is an explicit Cobb–Douglas assumption enabling tractable known-answer experiments.

For positive budget B and material weight w₁ = α + β:

$$
C^*=\frac{\alpha}{w_1}\frac B{p_C},
\qquad H^*=\frac{\beta}{w_1}\frac B{p_H}.
$$

Substitution gives the implemented indirect utility:

$$
\ln V=\ln\kappa+w_1(\ln B-\ln P)+\sum_{k=2}^{7}w_k\ln A_k,
\qquad
\kappa=(\alpha/w_1)^\alpha(\beta/w_1)^\beta.
$$

Value weights and spending shares enter separately:

$$
\alpha=w_1(s_{\mathrm{goods}}+s_{\mathrm{other}}),\qquad
\beta=w_1s_{\mathrm{rent}},\qquad \theta_k=w_k.
$$

Two equally material-oriented people can face different price pressure because their housing shares differ. Indirect utility already optimizes consumption allocation; it cannot directly score arbitrary actual spending chosen by an LLM. See [utility.py](../src/econ/utility.py) and [core.py](../src/econ/core.py).

### Scales, units, and double counting

Use interpretable physical/institutional anchors where possible; otherwise freeze reference-population quantile tables and compare alternative mappings. Save reference membership, year, and method. Do not rescale when the candidate set changes. See [ADR0007 (Chinese)](../DECISIONS/0007-absolute-anchoring-and-explicit-dispersion.md).

The economic core receives standardized records. Tax, country conventions, and currency conversion happen upstream. Log price ratios are dimensionless; budgets use consistent numeraires. Prices and material weight must be positive. Amenities use `MIN_AMENITY=1e-6` for logs; zero-weight terms use continuous extension.

Count each mechanism once: rent in the price index must not also be deducted from the same budget; diagnostic housing-burden ratios do not add a second utility penalty. [UtilityGraph](../src/econ/graph.py) records these paths.

The research code also retains a fixed-city, fixed-saving-rate transition:

$$
Y_{\mathrm{net},t}=Y_{\mathrm{gross},t}-Tax_t,\quad
B_t=(1-\rho)Y_{\mathrm{net},t},\quad
S_{t+1}=(1+i_{\mathrm{real}})S_t+\rho Y_{\mathrm{net},t}.
$$

Its wage/rent paths are exogenous and savings do not enter its terminal utility. This differs from product actions, savings goals, and terminal reference life. See [kernel.py](../src/econ/kernel.py).

### Welfare loss from a wrong choice

Choices may use estimated preferences, but both optimal and chosen cities are evaluated under the same person's truth. At reference prices and amenities equal to one:

$$
\ln e=\frac{\ln V-\ln\kappa}{w_1},
\qquad L=e(V_{\mathrm{best}})-e(V_{\mathrm{chosen}}).
$$

L is a reference-environment money-equivalent difference, not a wage compensation valid in every city. Keep log results; mark out-of-domain monetary interpretations missing. The single-value conversion guards both boundaries; the loss function still needs complete endpoint checks. Do not subtract estimator shrinkage floors from welfare loss.

$$
L_{\mathrm{norm}}=
\frac{\ln V_{\mathrm{best}}-\ln V_{\mathrm{chosen}}}
{\ln V_{\mathrm{best}}-\ln V_{\mathrm{worst}}}.
$$

An exact all-city tie has a zero denominator and is rejected. Report best–second gap, best–worst range, and dispersion separately. Difficulty and grouping use logs so monetary-conversion gaps do not remove particular people.

Three baselines exist: representative basket with material-only choice; substituted basket with true value weights; and personal basket with material-only choice. The last two diagnose mechanisms but do not add to a decomposition of the first. Dynamic baselines must additionally match cities, horizon, and information.

## Choice questions and information

Synthetic cities have correlated attributes. The main research persona distribution is:

$$
w\sim\operatorname{Dirichlet}(1.5,0.5,0.5,0.5,0.5,0.5,0.5),
\qquad \mu_P=(1/3,1/9,\ldots,1/9).
$$

This is an experimental design, not a population estimate. Research weights have no old product three-point floor. The occupation axis is fixed in this diagnostic; commuting remains and its identifiability is reported. See [ADR0006 (Chinese)](../DECISIONS/0006-asymmetric-dirichlet.md), [ADR0008 (Chinese)](../DECISIONS/0008-tax-amenity-coupling.md), [ADR0012 (Chinese)](../DECISIONS/0012-long-commute-experimental-arm.md), and [ADR0015 (Chinese)](../DECISIONS/0015-estimand-scheme-a-and-gate-inputs.md).

Questions include dominance, trade-offs, exact ties, and adversarial cases. Generators control gaps, ranges, and dispersion and report failed construction. See [worlds/](../src/worlds/).

O1 provides raw city/economic facts; O2 adds environment accounting; O3 adds representative-basket real income. Higher conditions retain all lower-condition fields, so O3 still includes amenities and persona information.

Persona presentation reserves implicit, mixed, and explicit variants; the current generator uses implicit prose. Hidden weights are absent, dimension order randomized, and generated text frozen and bound to truth. Scalar leakage tolerance is `1e-3`; seven-dimensional fingerprint tolerance is `4e-2`. See [ADR0001 (Chinese)](../DECISIONS/0001-leak-scan-tolerances.md). Product-confirmed preferences and research-prose information conditions serve different tasks.

<a id="noise"></a>
## Repeated choices and noise

For true log utility v:

$$
p_{itj}(\sigma)=\frac{\exp(v_{itj}/\sigma)}
{\sum_\ell\exp(v_{it\ell}/\sigma)},\qquad
H(p)=-\sum_jp_j\log p_j.
$$

Entropy describes diversity. All J alternatives exactly tied yield log J; partial ties still depend on remaining gaps and noise.

Calibrate noise using actual repeated-choice disagreement on the same questions:

$$
D(\sigma)=\operatorname{mean}_{i,t}
\left(1-\sum_jp_{itj}(\sigma)^2\right).
$$

This is an operational calibration, not permission to substitute matched-fit 1/γ₀. Independent repeats with equally sized choice sets have disagreement upper bound 1−1/J. See [ADR0003 (Chinese)](../DECISIONS/0003-sigma-calibration-by-disagreement.md).

<a id="recovery"></a>
## Preference recovery (P9)

Seven weights summing to one have six free dimensions. A diagonal covariance in seven-dimensional weight space ignores that constraint. The research specification uses additive log ratios with full covariance:

$$
a_{ik}=\log(w_{ik}/w_{ib}),\qquad
a_i\sim N(\mu_a,C_a),\qquad w_i=S_b(a_i).
$$

S restores the baseline coordinate and applies softmax. The material dimension is the baseline. Four fixed starts share integration points; simulated likelihood selects the solution. [fit_research](../src/eval/recovery/profiles.py) is used for formal recovery and floor simulations; bare `RecoveryConfig()` compatibility defaults are different. See [ADR0013 (Chinese)](../DECISIONS/0013-research-profile-alr-full-material.md).

Population covariance comes from the fitted distribution, not the sample covariance of individual posterior means, which also reflects individual information and shrinkage. Full covariance theoretically permits baseline changes, but numerical integration and optimization still require checks. Coordinates are clipped to ±40; per-start convergence is recorded. Absence of an exception does not imply convergence.

The historical difference-parameterization branch uses γ₀ = 1/σ and γk = wk/σ, a baseline column and six differences. Report small-denominator and out-of-range results rather than clipping them into valid weights. Diagonal ALR and `xlogit` remain diagnostic comparisons.

<a id="sampling"></a>
## Sampling and groups (P10)

P is the generated population, Q the frozen quota design, and S the realized sample. Main recovery evaluates S under Q; truth moments use that same sample:

$$
m_S=\frac1N\sum_iw_i,\qquad
C_{w,S}=\frac1N\sum_i(w_i-m_S)(w_i-m_S)'.
$$

Truth moments use `ddof=0`; the standard deviation of bootstrap replicates uses `ddof=1`. Stratify before choices by distance from the injected population mean, using an independent reference sample to freeze boundaries and probabilities. Never resample after seeing recovery outcomes.

Population reports reweight tail oversampling by design probabilities; within-stratum reports are conditional. Cross-stratum regression must state its target. Report weighting does not automatically change an unweighted model's estimand. See [ADR0014 (Chinese)](../DECISIONS/0014-p10-stratified-sampling-and-floor-reuse.md) and [ADR0015 (Chinese)](../DECISIONS/0015-estimand-scheme-a-and-gate-inputs.md).

Reuse fitted individual posteriors, group by injected truth or predefined features, and compare with same-design `floor_rmse`. If the floor is itself unreliable, mark `measurement_limited`. Do not fit a separate population model per person or form unstable individual ratios. Exploratory group findings need confirmation.

<a id="floor"></a>
## Model compression and estimator shrinkage (P8)

Recovered people may look alike because of the LLM or because limited choices shrink estimates. Generate choices from known truth and run the same estimator to measure its floor.

Compare fitted and sample-truth covariance through diagonal variance ratios and the Frobenius error. Report mean drift separately; do not divide near-zero off-diagonal elements.

Matched, shuffled, and synthetic-floor arms share one weight-space center:

$$
d_i=\|w_i-c\|,\quad u_i=(c-w_i)/d_i,\quad
proj_i=\langle\hat w_i-w_i,u_i\rangle.
$$

At zero distance, direction is undefined and receives the predefined missing status. Regress projection on distance to obtain matched, shuffled, and floor slopes:

$$
W_h(t)=s_{\mathrm{shuffled},h}-\lambda_h(t),\qquad
r_h(t)=\frac{s_{\mathrm{matched},h}-\lambda_h(t)}{W_h(t)}.
$$

Use the observed shuffled slope, not one. Do not clip r to [0,1] or fill invalid windows with zero. Shared geometry permits cancellation of shared attenuation; see [ADR0002 (Chinese)](../DECISIONS/0002-attenuation-cancelling-denominator.md). Shuffling is a persona-mismatch reference, not a strict bound for covariance ratios or arbitrary errors. Covariance and projection are mechanically related, not independent evidence.

<a id="scheme-a"></a>
### Scheme A: fixed center, alternative floor assumptions

$$
C_a(t)=(1-t)C_{a,S}+tC_{a,\mathrm{matched}},\qquad
E[S_b(\mu_a(t)+L_t\xi)]=c,\quad
L_tL_t'=C_a(t),\quad\xi\sim N(0,I).
$$

The center is the matched fitted distribution's weight mean. Sample ALR covariance uses the same baseline and `ddof=0`. Calibrate location after changing covariance to preserve that weight mean: nonlinear transformation means the transformed mean is not generally the mean of transformed values.

Recheck calibration using independent integration points for each t. The path need not be monotone; its endpoints are not strict bounds. See [scheme_a.py](../src/eval/floors/scheme_a.py) and [ADR0015 (Chinese)](../DECISIONS/0015-estimand-scheme-a-and-gate-inputs.md). The old `floors/path.py` remains for historical experiments.

### Joint bootstrap and valid windows

Resample whole people, including all their choices. Arms share indices; rebuild the center and path within each replicate:

$$
\operatorname{Var}(W)=\operatorname{Var}(s_{\mathrm{shuffled}})
+\operatorname{Var}(\lambda)
-2\operatorname{Cov}(s_{\mathrm{shuffled}},\lambda).
$$

Neither floor-only uncertainty nor independent variance addition is sufficient. Formal windows require at least 200 valid replicates; small runs test engineering. Report nonpositive-window frequency, valid-r count, and window standard error separately. Pointwise intervals are not simultaneous bands. The current 10% nonpositive-window rule is a design choice. Judge the entire path and both endpoints; retain uncertainty when intervals cross zero.

<a id="research-gates"></a>
## Three research gates

| Gate | Question | Interpretation |
|---|---|---|
| One | Does baseline welfare loss exceed questionnaire-perturbation noise? | Is improvement worth measuring? |
| Two | Does a valid window remain after estimator shrinkage? | Can λ, W, and r be interpreted formally? |
| Three | Does noise/shrinkage change with preference distance? | Report a total slope or conditional curves? |

Gate one averages repeated perturbation loss within a person, then takes a median across people. Also report flip probability and conditional loss: expected loss = flip probability × loss conditional on a flip. See [ADR0010 (Chinese)](../DECISIONS/0010-noise-floor-aggregation.md).

Current gate-two thresholds are λ/shuffled slope ≤ 0.8, shuffled slope ≥ 0.5, and W/SE(W) ≥ 1.96. These are design thresholds, not power guarantees. A nonsignificant gate three does not establish constancy everywhere. Small pilots may collect repeats before formal measurement. Missing input and insufficient valid computation are `pending_input` and `pending_computation` respectively.

These gates concern static research measurement, not the entire product acceptance process.

<a id="precision"></a>
## Numerical precision and reporting

Fit, posterior, and population-moment integration budgets have different roles. Compare posterior estimates at two budgets:

$$
\max_{i,k}\left|
\hat E[w_{ik}\mid y]^{(R)}
-\hat E[w_{ik}\mid y]^{(R_*)}
\right|.
$$

A tolerance of 0.005 is half a point on a 100-point preference scale. It measures numerical approximation, not model-versus-human error. The higher-budget reference still has error; effective sample size is not a true population sample size. Check group comparisons, rankings, and slopes as well. Low-budget bootstrap is not automatically conservative.

Reports record model version, prompts, reasoning settings, information conditions, design, seeds, failures/retries, subgroup results, and floors. For rolling aliases, save the actual returned version. See [index](INDEX.en.md), [reproduction](REPRODUCING.en.md), and [decisions (Chinese)](../DECISIONS/README.md).
