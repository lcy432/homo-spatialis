# Documentation index

English · [简体中文](INDEX.md)

Start here for the project, its mathematical models, city data, and implementation. English links below lead to English guides. Destinations available only in Chinese are explicitly marked.

<a id="reading-routes"></a>
## Reading routes

| What you want to understand | Start here |
|---|---|
| Motivation, experience, and scope | [Project README](../README.md) |
| Run locally and inspect outputs | [Reproduction](REPRODUCING.en.md) · [Web setup](../web/README.md) |
| Execution and evaluation | [Life-model mathematics](METHODS.en.md#life-kernel) → [Technical designs](design/README.md) |
| Sources and available choices | [Mapping A and B](DATA.en.md#mapping-methods) → [Data guide](DATA.en.md) → [Contributing](../CONTRIBUTING.md) |
| Locate code and make changes | [Architecture](ARCHITECTURE.en.md) → [Development](DEVELOPMENT.en.md) |
| Research questions and measurements | [Research methods](RESEARCH.en.md) · [References](REFERENCES.en.md) |
| Future releases | [Roadmap](../PLAN.en.md) |
| Attribution, citation, and release | [Authors](../AUTHORS.en.md) · [Citation](../CITATION.en.md) · [Release checklist](RELEASE.en.md) |
| Parameters and modeling assumptions | [Parameter register](design/parameter-assumption-register.en.md) |
| Earlier discussions and decisions | [History (Chinese)](history/README.md) · [Decisions](../DECISIONS/README.md) |

Coding agents begin with [AGENTS.md (Chinese)](../AGENTS.md) and [CLAUDE.md (Chinese)](../CLAUDE.md), then use these routes to find the relevant topic.

<a id="document-roles"></a>
<a id="governance"></a>
## Document responsibilities

The owner's latest explicit decision takes precedence. READMEs describe the purpose and current scope; the roadmap describes version direction; technical designs and effective decisions define mechanisms and protocols. Code establishes current behavior, while documentation records intended behavior and constraints. If they disagree, identify the discrepancy before deciding what to change. Historical documents describe earlier decisions and do not override current instructions.

The English technical guides explain current mechanisms and retain code references, formulas, parameter IDs, and links to the corresponding Chinese documents. Earlier development diaries, handoffs, and archived prompts remain in their original language.

<a id="layers"></a>
## Layers and names

| Layer | Responsibility | Earlier identifiers | Code | Guide |
|---|---|---|---|---|
| Life engine | Settlement, resources, health, skills, savings, scoring, and evaluation | E1-1–E1-6; E2 merged into this layer | `src/econ/life_*`, `src/common/life_*`, `src/ledger/life_*` | [Life model](design/e1-3-life-model.en.md), [resources](design/e1-4-time-energy.en.md), [career and risk](design/e1-5-career-savings-risk.en.md), [evaluation](design/e1-6-evaluation.en.md) |
| Agent | Observations, planning, model transport, budgets, and replay | E1-7A/B | `src/agentlayer/`, `src/orchestration/` | [Agent loop](design/e1-7-agent-loop.en.md) |
| Intake | Answers, occupations, initial states, real data, and shared messages | E3; M1/M1.5 | `src/product/`, `src/common/product_contracts.py`, `contracts/product/` | [User input](design/e3a-product-input.en.md), [data intake](design/intake-02-real-data.en.md), [shared contract](design/e3-e5-product-contract.en.md) |
| Interface | Forms, progress, results, and browser entry point | E5; M6 | `web/` | [Web guide](../web/README.md), [detailed web design (Chinese)](design/e5-web-ui.md) |
| Research | Preference recovery and loss measurement in controlled worlds | R/E4; M3/M5 | `src/eval/`, `src/worlds/` | [Research methods](RESEARCH.en.md) |

The older static welfare core in `src/econ/core.py`, `kernel.py`, and `utility.py` supplies the research benchmark. It is distinct from the monthly life engine. File names retain earlier identifiers to preserve links.

<a id="status"></a>
## Status and default entry points

Current product scope is described in the [README](../README.md#current-scope), planned versions in the [roadmap](../PLAN.en.md), and commands in the [reproduction](REPRODUCING.en.md) and [web](../web/README.md) guides.

The detailed module status log, default configurations, and development-round records remain in the [Chinese project index](INDEX.md#status). That log is maintained in one place so translated status tables do not drift. “Implemented” means code exists and the recorded checks passed; it does not establish real-world predictive validity.

## Language conventions

Use the English counterpart when reading or contributing in English, and the Chinese counterpart in Chinese. Active directories use `README.md` for English and `README.zh-CN.md` for Chinese; the contribution guide also defaults to English. Other topic guides retain their original Chinese paths and use `.en.md` for English. A language switch is available at the top of paired guides. Keep factual content, equations, code identifiers, and commands aligned when updating either version.

Historical archives, original decision records, and legacy test fixtures retain their original language. Links to those records retain an explicit language label.
