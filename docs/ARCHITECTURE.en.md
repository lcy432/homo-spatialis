# Architecture and code entry points

English · [简体中文](ARCHITECTURE.md)

Homo-Spatialis separates three questions: what choices are available, how to plan, and what happens when the plan runs. Data preparation supplies choices, the language model plans, and the life engine executes and evaluates. The browser, command line, and background service share that engine.

Start with the [README](../README.md) and [methods](METHODS.en.md). This guide locates code; [reproduction](REPRODUCING.en.md) covers execution and the [project index](INDEX.en.md) covers navigation and status.

## One simulation

~~~mermaid
flowchart TD
    A[Confirmed answers and city data] --> B[Compile profile and life catalogs]
    B --> C[Check inputs and confirm run settings]
    C --> D[Create independent clones in each city]
    D --> E[Annual facts and plan]
    E --> F[Monthly income, spending, resources and events]
    F --> G[Record and score actual life]
    G --> H{Another year?}
    H -->|Yes| E
    H -->|No| I[Evaluate trajectory and terminal state]
    I --> J[City comparisons, clone histories and archives]
~~~

1. **Prepare the same person.** Intake compiles confirmed answers, checks occupation and qualifications, and creates city-specific starting states. It retains identities for inputs, data, and rules.
2. **Confirm the run.** Preflight checks the pack, profile, supported scope, and settings. The service records start authorization and rechecks changed conditions. The outer workflow registers, queues, starts, and recovers requests.
3. **Plan each year.** Each clone receives current visible facts, options, personal state, and bounded factual memory. The planner returns a complete annual plan. Formatting or feasibility failures can trigger a bounded number of revisions.
4. **Execute each month.** Prices, taxes, time, and resource constraints determine work, housing, food, travel, learning, and activities. Actual records establish what happened; plans and explanations do not substitute for outcomes.
5. **Continue into the next year.** Updated state and bounded factual memory enter the next round. Clones run independently: they neither trade with one another nor jointly change wages or rents.
6. **Aggregate with provenance.** Evaluate the completed trajectory and terminal state, then produce city reports. Completion, life exit, and simulation missingness are counted separately. Results link back to clone, year, and month.

## Layer responsibilities

| Location | Responsibility |
|---|---|
| [src/common/](../src/common/) | Profiles, catalogs, plans, ledgers, public messages, and local object constraints |
| [src/ledger/](../src/ledger/) | Load data/configuration and validate cross-object references, units, and content |
| [src/product/](../src/product/) | Answers, occupations, qualifications, compilation, preflight, result projection; `realdata/` contains acquisition and mapping |
| [src/agentlayer/](../src/agentlayer/) | Permitted observations, planning requests, mock/real model transport, plan parsing |
| [src/econ/](../src/econ/) | Execute, account, check resources, score, and evaluate; economic formulas belong here |
| [src/orchestration/](../src/orchestration/) | Multi-city, multi-clone, multi-year runs, budgets, archives, recovery, and replay |
| [src/workflows/](../src/workflows/) and [src/product/service/](../src/product/service/) | Product request lifecycle: checks, consent, registration, background execution, delivery, sessions, jobs, quotas |
| [web/](../web/) | Forms, settings, progress, results; shared messages, with no independent life-score calculation |

There are two loops. `workflows` manages whether a product request is authorized, started, or finished. `orchestration` manages years of life. LangGraph belongs to the former; monthly economic computation belongs to the life engine invoked by the latter.

## Observation, execution, and scoring

**Agents see life facts.** [AgentObservation](../src/agentlayer/life_views.py) and [planning requests](../src/agentlayer/life_planning.py) expose allowed fields. Agents can see housing, wages, commutes, their state, and past events. Research truth, internal scoring parameters, and future events stay engine-side. [compact_payload.py](../src/agentlayer/compact_payload.py) compresses long payloads reversibly by section while retaining item identities, values, and conditions.

**Execution uses plans and environment facts.** [life_settlement.py](../src/econ/life_settlement.py) accounts for actual outcomes without changing accounts based on preference scores. Amounts use integer minor units and explicit numeraires; cross-currency or cross-year changes require an explicit conversion.

**Scoring reads actual results.** [life_year.py](../src/econ/life_year.py) connects settlement, scoring, and next-year state. Scoring uses confirmed preferences and actual consumption, activities, and resources. Unknown data remains unknown. Incomplete scoring, life exit, planning stops, and software faults have distinct states. City reports preserve the original clone count.

**Replay uses archives.** [life_archive.py](../src/orchestration/life_archive.py) stores plans, input bindings, and execution records. Replay checks results without asking the model again. Recovery resumes completed progress and calls the model only when a new plan is needed.

## Code reading routes

| Question | Entry points | Tests or design |
|---|---|---|
| How do answers become a runnable profile? | [answers.py](../src/product/answers.py), [qualifications.py](../src/product/qualifications.py), [compile.py](../src/product/compile.py) | [Ratings tests](../tests/product/test_ratings.py), [input design](design/e3a-product-input.en.md) |
| How do records become available choices? | [realdata/](../src/product/realdata/) | [Data guide](DATA.en.md), [assembly tests](../tests/product/test_life_pack_assembly.py) |
| How does the planner propose and revise plans? | [life_planning.py](../src/agentlayer/life_planning.py), [life_llm.py](../src/agentlayer/life_llm.py) | [Agent loop](design/e1-7-agent-loop.en.md), [runner tests](../tests/life/test_runner.py) |
| How do money, time, and energy constrain life? | [Settlement](../src/econ/life_settlement.py), [resources](../src/econ/life_resources.py) | [Settlement tests](../tests/life/test_settlement.py), [resource design](design/e1-4-time-energy.en.md) |
| How is life evaluated? | [Scoring](../src/econ/life_scoring.py), [evaluation](../src/econ/life_evaluation.py), [aggregation](../src/econ/life_aggregation.py) | [Scoring tests](../tests/life/test_scoring.py), [evaluation tests](../tests/life/test_evaluation.py), [evaluation design](design/e1-6-evaluation.en.md) |
| How does a request reach the engine? | [graph.py](../src/workflows/graph.py), [adapters](../src/workflows/e3_adapters.py), [worker](../src/workflows/service_worker.py) | [Wiring tests](../tests/workflows/test_e3_wiring.py), [service smoke tests](../tests/product_service/test_smoke_flow.py) |
| How do frontend and backend agree? | [Python contract](../src/common/product_contracts.py), [exports](../contracts/product/v3/), [web](../web/) | [Schema tests](../tests/product/test_contracts_schema.py), [shared contract design](design/e3-e5-product-contract.en.md) |

Python defines the shared messages. Exported JSON Schema and samples serve the frontend. `contracts/product/v3/` identifies the contract generation; revision details follow declarations and the [project index](INDEX.en.md).

## Life simulation and research

Life simulation asks what trajectory results from a person's answers, city data, and explicit rules. It uses annual decisions, monthly execution, and multi-period evaluation.

The repository also retains static research tools. [worlds/](../src/worlds/) generates synthetic environments and personas. [core.py](../src/econ/core.py), [kernel.py](../src/econ/kernel.py), and [utility.py](../src/econ/utility.py) calculate welfare under known preferences. [eval/](../src/eval/) studies recovered preferences, shrinkage toward population averages, and welfare loss. Controlled truth supports model evaluation; static welfare and the life simulation's score are different measurements. See [research](RESEARCH.en.md) and [references](REFERENCES.en.md).

## Where extensions belong

New cities generally change sources, mappings, and packs. New model providers generally change transport and configuration. A new life activity first needs defined resource costs and observable outcomes, then any necessary contract and economic-rule changes. Presentation-only changes belong in product display or the web app.

Formulas, proxies, and default parameters affect findings. Record changes and their rationale in the [parameter register](design/parameter-assumption-register.en.md) and related designs. Follow the [contribution guide](../CONTRIBUTING.md) and [maintainer/agent rules (Chinese)](../CLAUDE.md).
