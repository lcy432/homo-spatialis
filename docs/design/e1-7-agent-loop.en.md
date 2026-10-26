# E1-7: The annual planning loop

English · [简体中文](e1-7-agent-loop.md)

Each clone stays in its assigned city. Once a year, a planner proposes a complete plan; the engine checks it, executes it month by month and records the consequences. The planner does not calculate wages, prices, health outcomes or scores.

## One action year

1. Project the opening state, confirmed user statements, city facts, available options and permitted memory into a safe request.
2. Ask the planner for a structured annual action, a short needs summary and a rationale.
3. Parse the JSON and check feasibility without drawing new illness events.
4. Return safe diagnostics for repairable errors, within the fixed revision allowance.
5. Execute the accepted plan with actual events, then score and hand off the resulting state.
6. Preserve the records for evaluation, replay and the next year's observation.

The default is one initial planning attempt and at most two revisions per clone-year. Service retries and plan revisions have separate counters. Events during execution never trigger a return to the opening state to obtain a more favorable plan.

## What the planner sees

Visible facts include available options, prices and monetary bases, service duration, work arrangements, resource costs, qualifications, current cash, holdings, abilities, health, past events and public city background.

The request excludes internal preference truth, score coefficients, score objects, future random events, the illness-risk table and terminal reference plans/results. Public city facts and their internal score mappings are stored separately and checked for consistency before execution.

Observations are constructed field by field. Serializing an entire internal state and asking the model to ignore some fields is not the information boundary. User text is treated as data, not as instructions to change the protocol.

## Output and memory

The output must be one JSON object accepted by the planning contract. The runner supplies identity and year. Model-generated extra fields, invented results or executable code are rejected. Rationale text is retained for explanation but never read by settlement.

Memory distinguishes environment facts from the planner's own earlier summary. Facts come from permitted feedback, with bounded recent-year and per-record lengths. Self-reports are labeled as such. “I intend to become an accountant” cannot create a qualification.

Current holdings, balances and health come from the opening state, not a prose memory. Truncation is recorded; confirmed preference information is not reconstructed from a shortened narrative.

## Failure handling

| Situation | Handling |
|---|---|
| Invalid output or repairable plan | Safe diagnostics and a bounded revision |
| Revision allowance exhausted | Planning stops for that clone; earlier years remain |
| City-specific data problem | Affected clone stops with the data reason |
| Actual inability to afford minimum living | Life exit under the engine rules |
| Event-driven stop or incomplete feedback | Existing trajectory retained as incomplete |
| Shared configuration or unexpected program failure | Batch pauses |
| Global call or budget limit | Further model requests are blocked; original roster retained |

Diagnostics disclose only permitted facts. They do not reveal which option would score best. Missing clones are not replaced, and evaluation uses the original roster.

## Model transport and budgets

The provider adapter handles request formatting, sending, usage and finish reasons. Planning, validation, budgets and archives remain provider-independent. Effective planner settings must match the declared run settings before the first call.

Current roles and defaults live in [models.yaml](../../configs/models.yaml) and the run configurations. Follow [Development](../DEVELOPMENT.en.md) for authorized live-model testing. Offline mock runs do not call a real model.

Claude transport uses one Agent SDK turn without tools or settings-file access. Product roles may explicitly use rolling model names when no dated snapshot is available; actual returned model identifiers are recorded.

Paid runs reserve budget and record send intent before transmitting. An uncertain send is not blindly retried. Secrets are excluded from requests archived for inspection, logs and public errors.

## Parallelism, archives and replay

Clones can run independently in parallel. Shared controls enforce the global call cap and batch state; clones do not influence one another through mutable life state.

Each run archives input identities, requests, responses, plan checks, annual execution, evaluation and reports. Replay reuses saved planner responses and calls no model. Resume verifies identities and recorded sends before continuing. A partially initialized or changed archive is a conflict, not an invitation to start over silently.

The deterministic mock is a pipeline fixture with declared rules. Its success shows that the workflow executes; it does not establish language-model quality.

## Code and checks

| Responsibility | Entry point |
|---|---|
| Allowed observations | [life_views.py](../../src/agentlayer/life_views.py) |
| Requests, parsing, diagnostics and memory | [life_planning.py](../../src/agentlayer/life_planning.py) |
| Model transport | [life_llm.py](../../src/agentlayer/life_llm.py) |
| Runner and archive | [life_runner.py](../../src/orchestration/life_runner.py), [life_archive.py](../../src/orchestration/life_archive.py) |
| Verification | [Planning tests](../../tests/life/test_planning_interface.py), [runner tests](../../tests/life/test_runner.py), [offline live-adapter tests](../../tests/life/test_e1_7b_live_offline.py) |

See [Agent assumptions](parameter-assumption-register.en.md#agent-loop) and [Model evaluation](e1-6-evaluation.en.md).
