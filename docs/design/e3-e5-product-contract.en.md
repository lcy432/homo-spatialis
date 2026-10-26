# E3/E5: Shared product contract

English · [简体中文](e3-e5-product-contract.md)

The product passes a confirmed user profile and a documented city data pack to the life engine. The browser presents that process; it does not determine the economic results.

## Responsibilities

| Layer | Owns |
|---|---|
| E1 life engine | Actions, settlement, resources, illness, scoring and evaluation |
| E3 product/intake | Input semantics, confirmation, data compilation, preflight, public progress and results |
| Workflow | Ordering, confirmation state, run registration, recovery and delivery |
| Hosted service | Identity, access, quotas, queueing, retention and authorized execution |
| E5 interface | Forms, explanations, controls, progress and result navigation |

Shared Python contracts, exported schemas and public examples live in [contracts/product/v3](../../contracts/product/v3/). The browser consumes these messages rather than reading engine archives, database internals or workflow checkpoints.

## Personal input

Current inputs include occupation and structured qualifications, age, resources, independent ratings of six dimensions and four material components, interests, cuisines and health-coping answers. See [User input](e3a-product-input.en.md).

Raw answers, interpretation, confirmation and executable profile are separate objects. Acknowledging a model simplification is not a preference answer. Missing information stays missing. Free text does not create qualifications, numeric weights or prices.

The standard start keeps personal conditions comparable across cities while matching the confirmed occupation to each local catalog. It does not claim to transplant every feature of the person's real household or career.

## Run boundary

The service owns a run intent and its effective settings. Defaults, allowed ranges and the settings chosen for this run are separate. A start request is tied to a reviewed profile, data identities and required acknowledgments.

“One confirmation and start” records authorization to proceed when checks succeed. The worker starts only while that authorization remains valid. Changed conditions or a stop invalidate it and provide a reason. Repeating the same request with its idempotency key retrieves the same intent; it must not create another paid run.

Queries are read-only. Polling does not start work, renew retention, change quotas or request another model response.

## Results and denominators

Every city preserves the original roster N = C + E + M. Complete-clone means use C. Exit rates use N. Missing-simulation bounds exclude confirmed exits and retain unresolved simulations. See [Evaluation](e1-6-evaluation.en.md).

The interface presents city comparison, city details and clone-year records. It must distinguish incomplete computation from a person's inability to sustain minimum living. A missing value is not rendered as zero.

Progress reflects committed work. During simulation, the summary counts completed simulation years across clones. Input checks, queueing and result preparation have no fixed duration, so they are shown as stages rather than percentage estimates. The run is marked complete when its results are available.

## Display language

Business objects retain their stable identifiers and meanings. A versioned display layer supplies readable text at the corresponding field/entity location. Switching language does not change confirmation, execution or scores.

Historical results use their bound display-resource version, including map geography. Missing translations remain explicit; the interface must not combine a historical run with an unrelated current resource merely to fill a gap.

## Data and future countries

Money carries currency, base year and local-price meaning. Geography, occupation classification, tax jurisdiction and source periods remain explicit. Cross-country comparisons will require documented bridges and comparable scenarios rather than implicit currency conversion.

Current source data and mapping assumptions are documented in [Data](../DATA.en.md). Broader city contributions are planned for V1.1; richer daily-life mechanisms and interview-based input follow in [the roadmap](../../PLAN.en.md).

## Implementation and checks

- [Shared types](../../src/common/product_contracts.py) and [schemas/examples](../../contracts/product/v3/).
- [Product implementation](../../src/product/), [service](../../src/product/service/), [web](../../web/).
- [Schema checks](../../tests/product/test_contracts_schema.py), [product tests](../../tests/product/), [service tests](../../tests/product_service/).
- [Workflow design](langgraph-product-workflow.en.md), [hosted-service operations (Chinese)](e3c-service.md), [web design (Chinese)](e5-web-ui.md).

The operational records remain in the [status index (Chinese)](../INDEX.md#status).
