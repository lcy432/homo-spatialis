# Homo-Spatialis: simulation methods

English · [简体中文](METHODS.md)

Author: **Chengyu Liu**. Software version: **0.1.0**. This is a software methods report distributed with the source, not a peer-reviewed publication. See the [project index](INDEX.en.md#status) and [reproduction guide](REPRODUCING.en.md).

## 1. Motivation

How would the same person's constraints and opportunities change across cities? With environment rules, preferences, and evaluation recorded explicitly, we can study how language-model choices respond to a person and which information they overlook.

The initial release separates data, mapping assumptions, actions, settlement records, and evaluations for inspection. It provides a common basis for validation and city-data contributions. It does not establish where a real person will certainly live better.

## 2. Model structure

A confirmed user profile is used to create independent clones in several cities. Each clone makes an annual plan; the economic model carries it out and records the outcomes month by month. The default example lasts three years.

~~~mermaid
flowchart LR
    A[Sources and explicit assumptions] --> B[Review, confirm and compile answers]
    B --> C[Preflight and frozen inputs]
    C --> D[Permitted observations and annual plans]
    D --> E[Monthly execution and settlement]
    E --> F[Outcome scores and factual memory]
    F -->|Next year| D
    F --> G[Evaluation, counts and missingness]
~~~

- **Planning and settlement:** the planner chooses permitted options. Catalogs and configuration define prices, wages, taxes, and constraints. Given fixed inputs, plans, and events, settlement is reproducible.
- **Settlement and scoring:** execution accounts for actions and constraints; scoring reads outcomes rather than the persuasiveness of the plan. Amounts use integer minor units and explicit currency/base-year conventions.
- **Information boundaries:** agents receive permitted observations and bounded factual history. Hidden scores, research truth, and future events do not enter requests. Each clone gets one normal annual planning call and at most two revisions. Later adverse events do not trigger a new draw through replanning.
- **Unknowns and failures:** missing data is not zero. Positive-weight dimensions are not dropped and renormalized to manufacture a complete score. Reports preserve the original count N = C + E + M: complete evaluations, life exits, and simulation missingness.
- **Traceability:** compiled configuration, input snapshots, plans, archives, and submission identities remain linked. Recovery, replay, and duplicate submissions have defined behavior.

Implementation spans `src/product/`, `src/agentlayer/`, `src/orchestration/`, `src/ledger/`, and `src/econ/`. See [technical designs](design/README.md), the [parameter register](design/parameter-assumption-register.en.md), and [attribution](../AUTHORS.en.md).

<a id="life-kernel"></a>
## 3. Mathematical structure

[Mapping A and B](DATA.en.md#mapping-methods) turn city evidence into executable opportunities. The engine determines feasibility, updates resources and state, and evaluates what occurred. The final aggregate score is the end of this calculation.

| Module | Mathematical role | Section |
|---|---|---|
| E1-1 | State, actions, and information contracts | [State and cycles](#state-actions) |
| E1-2 | Money conservation, taxes, minimum living costs | [Settlement](#settlement) |
| E1-3 | Functional outcomes, nonlinear component scores, preferences | [Monthly evaluation](#monthly-scoring) |
| E1-4 | Time/energy constraints and annual feedback | [Resources](#resources) |
| E1-5 | Learning, savings targets, health transitions | [Career and risk](#career-savings-risk) |
| E1-6 | Terminal reference life, time aggregation, city reporting | [Terminal evaluation](#terminal-evaluation), [city aggregation](#city-aggregation) |

<a id="state-actions"></a>
### 3.1 State and decision cycles

Let x denote cash, possessions, health, learning progress, qualifications, and resource allowances at the start of a month. Given annual plan a, city catalog D, monthly event ε, and rule parameters θ:

$$
x_{m+1}=F(x_m,a_y,D_c,\varepsilon_m;\theta).
$$

This notation summarizes the program: the LLM chooses at the start of the year; F executes monthly. Qualifications, cash, time, and energy constrain actions. Scoring reads executed records, and annual energy feedback affects the following year's state. Fixed plans, inputs, and events can be replayed. See [architecture](ARCHITECTURE.en.md).

<a id="settlement"></a>
### 3.2 Cash, taxes, and minimum living costs

For a settled month:

$$
b_{m+1}=b_m+Y_m-\Delta T_m-\sum_{a\in A_m^{\mathrm{executed}}}C_{a,m}.
$$

Y is actual gross earnings and C is actual expense. Taxes use cumulative earnings across all jobs in the same tax year:

$$
\Delta T_m=\mathcal T_c(Y_{\mathrm{cum},m})
-\mathcal T_c(Y_{\mathrm{cum},m-1}).
$$

The pack binds tax brackets, deductions, and payroll rules. Progressive taxation, income caps, and stopping work midyear therefore share one ledger. The six-city pack uses implemented official 2024 rules; synthetic examples use a separate test tax system.

After earnings and taxes, the engine checks whether cash can cover minimum housing and food together. It then pays actual housing, food, and other planned items. Insufficient cash triggers defined fallback or rejection; inability to cover the minimum produces a life exit. Future wages cannot be spent early, and the minimum-cost check itself does not deduct money.

Amounts are stored in cents. Annual amount A is allocated through cumulative rounding:

$$
A_m=\operatorname{round}(Am/12)-\operatorname{round}(A(m-1)/12).
$$

The twelve months sum exactly to A. See [settlement code](../src/econ/life_settlement.py) and [tests](../tests/life/test_settlement.py).

<a id="resources"></a>
### 3.3 Time, energy, and feedback

Each month must satisfy both constraints:

$$
\sum_a h_{a,m}\le H_y/12,
\qquad
\sum_a\lambda_a h_{a,m}\le E_y.
$$

H is annual allocatable time; h is time actually used by work, commuting, cooking, chores, learning, activities, or care. Activity-specific λ converts hours into burden points; E is the year's monthly energy allowance. Months cannot borrow allowances. Unused time does not automatically produce rest benefits.

For a chore, equipment and services are alternative ways to reduce personal time:

$$
h_{j,m}^{\mathrm{personal}}
=\min(h_j^{\mathrm{baseline}},h_{j,1}^{\mathrm{residual}},h_{j,2}^{\mathrm{residual}},\ldots).
$$

Two substitutes do not deduct the same task twice. Commutes depend on housing, actual jobs, and currently available modes. The rule chooses the fastest available round trip, breaking time ties by lower cost. Actual attendance for every job enters both time and expense accounts.

When enabled, complete average satisfaction in year y determines next year's energy:

$$
E_{y+1}=\left\lfloor E_{\mathrm{base}}(1+\alpha\bar S_y)\right\rfloor_{0.01}.
$$

The floor rounds down to 0.01 burden points. Every year starts from the same base, so feedback does not compound. α is a modeling parameter. Incomplete scoring prevents enabled feedback from being computed.

See [resources](../src/econ/life_resources.py), [commuting](../src/econ/life_commute.py), [feedback](../src/econ/life_feedback.py), [resource tests](../tests/life/test_resources.py), [feedback tests](../tests/life/test_feedback.py), and the [resource design](design/e1-4-time-energy.en.md).

<a id="career-savings-risk"></a>
### 3.4 Learning, savings goals, and health

**Learning changes future qualifications.** Course progress uses actual attendance:

$$
p_{k,m}=\min\left(1,\frac{n_{k,m}^{\mathrm{attended}}}{n_k^{\mathrm{required}}}\right).
$$

Payment, attendance, completion, and qualification activation are separate events. Completion awards declared abilities for job eligibility from the next action year. Jobs are checked against the year's opening state; earnings come from selected, executed work. There is no hiring probability or competition for vacancies.

Monthly ability progress is:

$$
g_m=\sum_a(p_{a,m}-p_{a,m-1}).
$$

Existing abilities have progress one. For an unearned ability, related courses contribute their maximum completion fraction; two half-completed courses do not create a full qualification. Progress contributes to development scoring, while activated ability changes job eligibility at its own effective time.

**Savings goals track actual payment.** A goal specifies an item, quantity, and deadline and uses the shared cash balance. It is fulfilled only when the linked purchase or tuition payment is made in full. Reaching the target balance alone does not fulfill the goal or guarantee affordability after living costs. Goals neither create separate accounts nor reserve funds, and saving intentions do not earn a separate score. Remaining cash matters through future execution and terminal evaluation.

**Health transitions monthly.** New-illness probabilities in a healthy month depend on age band and the previous complete month's relative work burden:

$$
(p_{\mathrm{minor}},p_{\mathrm{major}})
=R\left(\mathrm{age\ band},\frac{E_{\mathrm{work},m-1}}{E_{m-1}}\right).
$$

Using one uniform draw, major illness occurs below its probability; minor illness occupies the next interval up to the sum; otherwise no new illness occurs. Existing illness follows duration, treatment, and recovery rules. Treatment costs, recovery time, and sick-pay effects enter the same accounts and resource constraints. Annual plans specify contingencies in advance.

For a given person, replicate, and month, the same random draw is used across cities. Different risk and resource conditions may still produce different outcomes. Using the previous month's work burden avoids making current illness risk depend on work already reduced by that illness.

See [career](../src/econ/life_career.py), [savings](../src/econ/life_savings.py), [health](../src/econ/life_health.py), their [career](../tests/life/test_career.py), [savings](../tests/life/test_savings.py), and [health tests](../tests/life/test_health.py), and the [technical design](design/e1-5-career-savings-risk.en.md).

<a id="monthly-scoring"></a>
### 3.5 Monthly life evaluation

Scoring first reads actual housing, consumption, participation, and state. Many components use capped saturation:

$$
z_{k,m}=a_{k,c}+\sum_r\eta_{k,r}x_{r,m},
\qquad
s_{k,m}=\min\left(c_k,\frac{z_{k,m}}{z_{k,m}+\tau_k}\right).
$$

The city background is a; actual functions or quantities are x; η converts them to the component scale. τ controls diminishing gains and c caps the response. Prices constrain purchases; scoring reads what was obtained and used.

| Component | Inputs and mapping |
|---|---|
| Housing | Space sufficiency and within-cell condition tier, mapped with their own coefficients; sparse cells borrow a broader condition distribution |
| Food | Fresh meals F and preference-matching meals T: z = ηf F + ηt T, then saturation |
| Daily support | Personal chore hours saved; communication, connectivity, and consumables use actual functional coverage and declared weights |
| Consumption and enjoyment | Actual functions or quantities; existence functions are deduplicated and quantities accumulated by unit |
| Leisure | City background plus activity hours, with a declared interest-match bonus, then saturation |
| Development | City background plus current ability-progress gains |
| Safety and environment | City safety; environmental physical conditions followed by declared nature bonuses and caps |

Leisure's activity contribution is:

$$
\eta_h\sum_a h_a\left(1+\kappa\mathbf1_{\mathrm{interest\ match}}\right).
$$

Transport uses a separate decreasing function. D is monthly attendance and T the attendance-weighted round-trip minutes:

$$
T_m^{\mathrm{eq}}=
\left[\rho+(1-\rho)\frac{D_m}{D_{\mathrm{ref}}}\right]T_m,
\qquad
s_{\mathrm{transport},m}=\frac{\tau_T}{\tau_T+T_m^{\mathrm{eq}}}.
$$

With no attendance, T uses the housing area's usual resident round-trip minutes. ρ retains background non-work travel. This is a scoring measure; actual time accounts use actual attendance.

Personal preferences enter after component scoring:

$$
s_{\mathrm{material},m}=\sum_jv_js_{j,m},\qquad
B_m=\sum_{k=1}^{6}w_ks_{k,m},\qquad
S_m=(1-\gamma)B_m+\gamma H(q_m).
$$

Four weights v aggregate material life; six weights w aggregate material life, safety, environment, leisure, development, and transport. Both sets are normalized from user-confirmed independent ratings. Health is separate: current H is 1 for healthy, 0.5 for minor illness, and 0 for major illness, with shared γ = 0.2. Unknown positive-weight components remain incomplete; weights are not redistributed.

See [scoring code](../src/econ/life_scoring.py), [configuration](../configs/life/scoring_product.yaml), [scoring tests](../tests/life/test_scoring.py), [extension tests](../tests/life/test_life_extension.py), [scoring design](design/e1-3-life-model.en.md), and [food and hobbies](design/e1-8-life-extension.en.md).

<a id="terminal-evaluation"></a>
### 3.6 Multi-period and terminal evaluation

Actual life uses an equal-weight monthly average, with T = 36 by default:

$$
L=\frac1T\sum_{m=1}^{T}S_m.
$$

Cash, skills, and equipment still matter at the end. Starting from the actual terminal state, the same settlement and scoring rules evaluate a predefined set of reference plans, each lasting H = 12 months:

$$
V(x_{\mathrm{terminal}})=
\max_{p\in\mathcal P(x_{\mathrm{terminal}})}
\frac1H\sum_{k=1}^{H}S^{\mathrm{ref}}_{p,k}.
$$

This maximizes over a finite feasible, fully evaluable reference set. Reference life uses no LLM, starts or continues no courses, and introduces no new illness. Existing illness continues; retained cash, qualifications, and equipment remain usable. Assets left after reference life receive no additional terminal bonus. If an unknown-data branch prevents determination, V remains missing rather than silently dropping that branch.

$$
J=\frac{TL+HV}{T+H}=\frac{36L+12V}{48}.
$$

Late learning or saving can therefore affect the result. Reference-plan scope, horizon, and restrictions determine how much of that benefit is recognized. See [terminal code](../src/econ/life_terminal_value.py), [evaluation](../src/econ/life_evaluation.py), [tests](../tests/life/test_evaluation.py), and [evaluation design](design/e1-6-evaluation.en.md).

<a id="city-aggregation"></a>
### 3.7 City aggregation and missing simulations

Preserve N = C + E + M: complete evaluations, confirmed life exits, and simulation missingness. Report the completed-clone mean and exit share separately:

$$
\bar J_C=\frac1C\sum_{i\in\mathcal C}J_i,
\qquad
r_E=\frac EN.
$$

When C = 0, there is no completed-clone mean. For missing clone i, declared bounds determine how its unknown score could affect the non-exit aggregate:

$$
\left[
\frac{\sum_{i\in\mathcal C}J_i+\sum_{i\in\mathcal M}\ell_i}{C+M},
\frac{\sum_{i\in\mathcal C}J_i+\sum_{i\in\mathcal M}u_i}{C+M}
\right].
$$

This is a missingness bound, not a sampling confidence interval. Confirmed life exits stay outside it. See [aggregation code](../src/econ/life_aggregation.py) and the [evaluation design](design/e1-6-evaluation.en.md).

### 3.8 Assumptions and calibration

Conservation, consistent units, and confirmed answers constrain computation. Saturation, health weights, energy feedback, qualification timing, illness risk, and reference horizons express modeling choices. Stronger feedback can carry early advantages into later feasible plans; shorter reference horizons can truncate delayed benefits.

Future calibration will use observations to estimate parameters and functional relationships, examining life rules and data mappings separately. Personal preferences and evaluation criteria require separate treatment. Rescoring a fixed trajectory differs from changing feedback and rerunning the trajectory. See the [parameter register](design/parameter-assumption-register.en.md) and [research questions](RESEARCH.en.md#research-questions).

## 4. Related work

| Work | Focus | This project's emphasis |
|---|---|---|
| Park et al., Generative Agents (2023) | Memory, reflection, planning, and believable social behavior | Annual choices, explicit economic settlement, and inspectable outcome evaluation |
| Piao et al., AgentSociety (2025; 2026 revision) | Large-scale social agents and computational social experiments | Independent versions of one person across cities; no clone social network or general equilibrium |
| Wang et al., Agentopia (2026) | Long-term social life and learning from simulation | Choices and evaluation under given preferences and constraints; the initial release does not validate model training or social emergence |

Sources: [Generative Agents](https://arxiv.org/abs/2304.03442), [AgentSociety](https://arxiv.org/abs/2502.08691), [Agentopia](https://arxiv.org/abs/2606.07513). See [references](REFERENCES.en.md) and [research methods](RESEARCH.en.md).

## 5. Validation scope

The [reproduction guide](REPRODUCING.en.md) supplies a successful synthetic flow and expected input rejections. The successful path covers review, confirmation, compilation, preflight, submission, three-year execution, and result projection. Rejections check missing data and incompatible conventions.

| Evidence | What it supports | What it does not establish |
|---|---|---|
| Mock synthetic flow | Fixed inputs execute and yield inspectable results | A real LLM understands a particular user |
| Controlled rejections | Specified missingness and inconsistency are handled | Every real-data error has been found |
| Engineering tests | Tested invariants and behavior | Predictive accuracy of city rankings |
| Sourced mappings | Values and transformations can be traced | Every field is calibrated or representative |

Research recovery and welfare-loss tools have separate gates. Research experiments excluded from the default suite are not revalidated by an engineering test run.

## 6. Limitations and future validation

Synthetic parameters are exploratory. Real sources still have gaps, proxies, mismatched geographies and dates, and unvalidated assumptions. Health, skill growth, and satisfaction-to-energy feedback need calibration and sensitivity analysis. Clones do not interact or change market prices through migration.

Model requests can fail through infeasible plans, formatting, service errors, or budget limits. These enter failure reports. Model-version changes affect reproducibility. Completed-clone means must be read with counts, exits, and missingness.

Future work will specify observations and error measures, calibrate mechanisms, compare parameterizations and mappings, and assess search or learning methods on independent data and simple baselines. Report performance by group as well as overall.

## 7. Citation and records

Use [CITATION.cff](../CITATION.cff) and identify the software version, data pack, configuration, and planner. Cite external data sources separately. Release and Zenodo records preserve the version; a DOI is added when the archive creates it.
