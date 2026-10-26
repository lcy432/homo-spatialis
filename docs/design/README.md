# Technical designs

English · [简体中文](README.zh-CN.md)

These guides connect mechanisms to equations, source data, code and tests. Start with [Model and evaluation](../METHODS.en.md), [Architecture](../ARCHITECTURE.en.md) or [Data and mapping](../DATA.en.md). The full reading routes are in the [documentation index](../INDEX.en.md).

## By topic

| Topic | Guides |
|---|---|
| Life scoring | [Living conditions and satisfaction](e1-3-life-model.en.md) |
| Monthly constraints | [Time, effort and feedback](e1-4-time-energy.en.md) |
| Longer-term consequences | [Career, savings and illness](e1-5-career-savings-risk.en.md) |
| Evaluation | [Life trajectories, terminal value and city summaries](e1-6-evaluation.en.md) |
| Food and hobbies | [Food plans, equipment and participation](e1-8-life-extension.en.md) |
| Planning | [Annual agent loop](e1-7-agent-loop.en.md) |
| Product input | [Answers and confirmation](e3a-product-input.en.md), [shared product contract](e3-e5-product-contract.en.md) |
| Source data | [Intake database](intake-02-real-data.en.md) |
| Executable city pack | [Assembly](e3b-life-pack.en.md), [A/B mappings](e3b-mapping-and-daily-support.en.md), [commuting](e3b-commute-data.en.md), [opportunities and careers](e3b-items-data.en.md) |
| Workflow | [LangGraph orchestration](langgraph-product-workflow.en.md) |
| Service and interface | [Web setup](../../web/README.md), [service operations (Chinese)](e3c-service.md), [web design (Chinese)](e5-web-ui.md) |
| Assumptions | [Parameter and assumption register](parameter-assumption-register.en.md) |
| Research | [Research methods](../RESEARCH.en.md), [references](../REFERENCES.en.md) |

File names retain the earlier E1/E3 identifiers so existing links keep working. The English guides describe current mechanisms. The Chinese originals also retain early worked examples and development records; obsolete configuration names in those records are not current run instructions.

## Maintaining these guides

Explain what a mechanism represents, how it is calculated, which evidence supports it, where it is implemented and what its checks establish. Identify omissions and the people or comparisons they may affect.

Keep paired guides consistent when changing behavior, equations or commands. Update the relevant parameter IDs when assumptions change. Maintain detailed implementation status in the [status log (Chinese)](../INDEX.md#status).

Earlier decisions, handoffs and task prompts remain in their original language: [decisions](../../DECISIONS/README.md), [history (Chinese)](../history/README.md), [design history (Chinese)](history/), [prompts (Chinese)](prompts/).
