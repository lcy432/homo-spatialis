# Shared product contracts

English · [简体中文](README.zh-CN.md)

This directory contains the current message schemas, public examples, and service API specification. The directory name stays `v3`; [`index.json`](index.json) records the exact version. The current export is `e3a-product-schema-v3.5`, with questionnaire v0.5, vocabulary v5, conversion v4, and service API v1.4.

## Files and validation

| File | Purpose |
|---|---|
| `*.schema.json` | 35 message schemas exported from [the Python contract definitions](../../../src/common/product_contracts.py) |
| [`index.json`](index.json) | Version identifiers and schema hashes |
| [`samples/`](samples/) | 173 public examples; each contains `message`, `description_zh`, and a schema-validated `payload` |
| [`openapi.json`](openapi.json) | HTTP API exported from the hosted service |

Run from the repository root:

~~~sh
uv run --frozen python scripts/product_e3a.py check-schema
uv run --frozen python scripts/product_service.py check-openapi
~~~

Schemas and OpenAPI are generated files. Change the authoritative Python definitions, then export them with `scripts/product_e3a.py export-schema` and `scripts/product_service.py openapi`. Frontend types are generated separately; see the [development guide](../../../docs/DEVELOPMENT.en.md#contracts-and-generated-files).

## Reading the examples

All example people are synthetic. Early examples use synthetic cities; examples such as [51](samples/51_e3b_preflight_ready.json) and [104](samples/104_service_e3b_result.json) use the frozen six-city US pack. Simulations use an offline mock or an explicitly identified test transport. Example credentials are invalid test values.

The samples cover profile review and confirmation, preflight, life records, evaluations, service sessions, drafts, run settings, starting and stopping, deletion, and progress. They illustrate messages and behavior, not real-model performance. Some earlier sample descriptions retain the vocabulary used when they were first added; use `index.json` and the current schemas for the active contract.

## Related guides

- [Shared contract design](../../../docs/design/e3-e5-product-contract.en.md) and [user input](../../../docs/design/e3a-product-input.en.md)
- [Life extensions](../../../docs/design/e1-8-life-extension.en.md) and [the six-city pack](../../../docs/design/e3b-life-pack.en.md)
- [Web setup](../../../web/README.md) and [service operations (Chinese)](../../../docs/design/e3c-service.md)
- [Earlier v3 changes (Chinese)](README.zh-CN.md#earlier-changes), [archived v2 (Chinese)](../v2/README.md), and [archived v1 (Chinese)](../v1/README.md)

The earlier change notes and archived exports remain in their original language. Current implementation status is maintained in the [status log (Chinese)](../../../docs/INDEX.md#status).
