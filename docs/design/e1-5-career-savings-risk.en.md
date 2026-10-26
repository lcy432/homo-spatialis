# E1-5: Career, savings and illness

English · [简体中文](e1-5-career-savings-risk.md)

These mechanisms carry consequences from one month and year into the next. They distinguish gaining an ability, paying for a course, holding enough cash and actually receiving a service.

## Learning and job eligibility

Courses specify tuition, monthly participation requirements, duration and the abilities they grant. Payment, attendance, completion and activation are separate events. Progress follows actual participation:

$$
p_{c,m}=\min\!\left(1,\frac{n_{c,m}}{N_c}\right).
$$

n is the number of completed participation months and N is the course requirement. Interrupted progress is retained; paid tuition is not charged again merely because the course resumes in another year.

An ability granted after completion becomes usable in the next action year. Job eligibility is evaluated against the opening state of the year. Completing a course in June does not authorize a January job choice. Eligible jobs are available under the catalog's rules; the current engine does not simulate applicant competition, vacancies or stochastic hiring.

For growth satisfaction, an ability already held has progress 1. Otherwise its progress is the maximum across courses granting that ability. Monthly growth is:

$$
g_m=\sum_b (p_{b,m}-p_{b,m-1}),\qquad
z_{\mathrm{development},m}=b_{\mathrm{city}}+\eta_{\mathrm{growth}}g_m.
$$

The current coefficient is η_growth = 12. Two courses granting the same ability do not double its progress. A course granting two distinct abilities can produce two increments, so vocabulary granularity matters. Certificates and salary increases do not receive additional direct satisfaction rewards.

Qualifications can be general, occupational or employer-specific. The scope recorded in the catalog determines which jobs they unlock. The real-data mappings are described in [Life opportunities and careers](e3b-items-data.en.md).

## Savings goals

A savings goal records an intended one-time durable purchase or course tuition. It does not open another bank account, reserve cash or create interest.

At month end:

$$
\mathrm{gap}_m=\max(0,\mathrm{quote}_m-\mathrm{cash}_m).
$$

Enough cash to cover the quote does not guarantee affordability after living costs. An unknown quote remains unknown.

The planner can set, keep, cancel, replace or reschedule a goal. Fulfillment requires the linked action to execute and pay the full required amount. Merely reaching the target balance, writing a goal in a plan, or buying an unlinked item does not fulfill it. Paying tuition fulfills the payment goal; it does not mean the course has been completed.

An overdue goal remains a recorded intention. It does not trigger an automatic purchase, a life exit or a satisfaction penalty. Goal histories do not alter the cash ledger.

## Illness events and paired comparisons

New illness can occur in a healthy month. Risk depends on age and work burden from the preceding complete month, using the declared health rules. For a given person, replicate, and month, the same random draw is used across cities; different city histories can still produce different thresholds and outcomes.

For a uniform draw u, major illness occupies the first interval, minor illness the next, and the remainder is healthy:

$$
\mathrm{event}=
\begin{cases}
\mathrm{major},&u<p_{\mathrm{major}},\\
\mathrm{minor},&p_{\mathrm{major}}\leq u<p_{\mathrm{major}}+p_{\mathrm{minor}},\\
\mathrm{none},&\text{otherwise}.
\end{cases}
$$

Only one illness is active at a time. Ongoing illness keeps its elapsed duration across years. Treatment and natural recovery take effect at month end; neither retroactively restores that month's wages or participation.

Under the current exploratory rules, minor illness permits work subject to resources; major illness causes a month of unpaid leave and pauses active participation. Recovery itself consumes time and effort. These are explicit simulation rules, not medical estimates.

## Contingency plans and treatment

The annual plan declares whether to seek treatment for minor and major illness and which activities to pause if resources become insufficient. It may also identify purchases to cancel during illness. The engine does not infer these choices from a preference score.

Execution checks minimum living costs, pays for housing and food, then handles treatment before other actions. If necessary, declared activities are paused in order. If treatment still cannot fit, it is dropped; if the remaining plan still cannot fit, the clone stops. A treatment data gap is not reported as inability to afford care.

Before the year begins, feasibility is checked without drawing new illness events. If illness occurs during execution, earlier months remain committed. A stopped month is not committed, so its provisional income and expenses do not enter the ledger.

## Health in scoring

The six life dimensions form B; the direct health term uses the month's opening condition:

$$
S_m=0.8B_m+0.2H_m,\qquad
H(\mathrm{healthy},\mathrm{minor},\mathrm{major})=(1,0.5,0).
$$

Treatment expenditure does not add points. Treatment affects subsequent health and feasible activity. The user's health-risk coping answer is available to the planner but does not control illness probability, recovery speed or the health weight.

## What remains simplified?

Learning lacks skill decay and a general model of transfer between occupations. Job access is deterministic once requirements are met. Savings use a single cash account without borrowing or investment returns. Illness rules omit many diagnoses, insurance arrangements and clinical differences. These omissions can matter especially for people with interrupted training, constrained credit or ongoing health conditions.

The [parameter register](parameter-assumption-register.en.md#long-term) separates these assumptions from observed inputs. Terminal treatment of skills, money and ongoing illness is described in [E1-6](e1-6-evaluation.en.md).

## Implementation and checks

- [Career](../../src/econ/life_career.py) and [career tests](../../tests/life/test_career.py).
- [Savings](../../src/econ/life_savings.py) and [savings tests](../../tests/life/test_savings.py).
- [Health](../../src/econ/life_health.py) and [health tests](../../tests/life/test_health.py).
- [Settlement](../../src/econ/life_settlement.py) integrates these mechanisms with the same cash and resource accounts.
