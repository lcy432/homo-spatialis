# Product workflow with LangGraph

English · [简体中文](langgraph-product-workflow.md)

LangGraph coordinates preflight, confirmation, registration, execution and delivery. It does not calculate life outcomes or make planning decisions.

## Flow

~~~mermaid
flowchart LR
  A[Confirmed profile and inputs] --> B[Preflight]
  B --> C[Confirmation]
  C --> D[Register once]
  D --> E[Start or resume engine]
  E --> F[Project and deliver result]
  B --> G[Revise inputs]
  E --> H[Needs attention]
~~~

The E3 adapter loads referenced objects, verifies their identities and invokes the existing product/engine entry points. The hosted worker drives this workflow with the service's authorization and execution controls.

## State and identity

The SQLite checkpoint stores workflow version, object references and identities, preflight reference, confirmation, idempotency key, run identifier and public attention codes. It does not duplicate cash, illness, abilities, annual scores or result bodies.

After registration, the run binding cannot change. A different returned run identifier is a conflict. Stale profile references, changed compiled inputs and altered archives are rejected before execution or projection.

A workflow `thread_id` identifies a workflow, not a person or an access right. The hosted service supplies authentication and authorization.

## Confirmation and recovery

Each transition checks the current stage. Repeated confirmation when nothing is pending does not advance the workflow. A confirmation refers to the exact preflight it approved.

Registration is idempotent. If the process stops after registration but before checkpointing it, recovery finds the same run. If execution finishes before delivery is recorded, recovery reads and projects the existing result without calling the planner.

An uncertain send, exhausted budget or credential issue remains a reason to stop. Workflow retry does not override the engine's recovery rules. There is no automatic node retry that could duplicate a paid call.

The local demonstration adapter uses mock planning. Hosted execution injects the configured planner through the service worker. The early standalone prototype's limitations should not be confused with the later hosted integration.

## Local inspection

The demonstration entry point is [product_workflow.py](../../scripts/product_workflow.py). It can seed a synthetic example, start a workflow, show its public state, confirm, resume or resolve an attention state. See the script's `--help` and [Reproducing runs](../REPRODUCING.en.md) for the current setup.

The public view exposes stage, what it is waiting for, preflight, confirmation reference, run identifier, progress, result and safe error codes. It does not expose internal exceptions, local paths or credentials.

The standalone workflow database is intended for one advancing process. Hosted coordination adds the worker and service controls; it does not turn arbitrary simultaneous writes to a prototype workspace into supported operation.

## Verification

Offline subprocess tests interrupt the process while awaiting confirmation, after registration, during planning and after engine completion. They check that recovery preserves identities and reuses recorded replies. Corrupted archives and conflicting bindings must fail without rewriting their source.

Equivalent direct and workflow paths are compared using the same mock inputs. This verifies orchestration equivalence, not real-model decision quality or recovery at every possible machine instruction.

| Responsibility | Entry point |
|---|---|
| Graph and runtime | [graph.py](../../src/workflows/graph.py), [runtime.py](../../src/workflows/runtime.py) |
| E3 adapter | [e3_adapters.py](../../src/workflows/e3_adapters.py) |
| Hosted worker | [service_worker.py](../../src/workflows/service_worker.py) |
| Tests | [Workflow tests](../../tests/workflows/), [service recovery tests](../../tests/product_service/test_recovery.py) |

See [Shared contract](e3-e5-product-contract.en.md) and [Agent loop](e1-7-agent-loop.en.md) for the boundaries on either side.
