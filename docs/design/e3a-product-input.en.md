# E3-A: User input and engine adaptation

English · [简体中文](e3a-product-input.md)

The input layer preserves what the user said, explains what the simulation can use, and obtains confirmation before creating an engine profile. It does not infer hidden preferences from the result it hopes to produce.

This guide describes the current input contract. Earlier percentage-allocation examples in the Chinese edition belong to an older questionnaire.

## From answers to a run

A `UserInputDraft` retains raw answers and their states. A `ProfileReview` explains recognized concepts, missing information, unsupported content and required acknowledgments. Confirmation creates a `ConfirmedProfile` with exact values, provenance and a content identity.

Compilation combines that profile with a data pack, standard initial conditions and a frozen roster. Preflight checks engine compatibility, preference coverage, resource/data readiness and run settings. The engine then executes the run; the product layer projects its results.

## Independent ratings

Users rate each of the six life dimensions from 0 to 100. The four material subcomponents use the same independent-rating approach. For completed ratings r with a positive sum:

$$
w_k=\frac{r_k}{\sum_j r_j},
\qquad
\mathrm{display\ weight}_k=100w_k.
$$

The system preserves both raw ratings and the exact normalized weights. These are preference weights, not spending shares. Required unanswered or uncertain inputs remain incomplete. All-zero ratings are rejected; equal importance is an explicit user action, not a fallback.

If the material parent weight is zero, its subcomponent answers are still retained. The compiler does not erase them or infer replacements. Exact fractions govern calculations; rounded percentages are display values.

## Occupation and qualifications

The current input catalog groups supported occupations and provides search terms. Users select an occupation and answer structured education, certificate and relevant-experience questions.

Confirmed education grants the declared level and lower levels. Certificates and experience follow the existing data-pack mappings. A current occupation does not grant an unreported degree or license. Unsupported occupations are not substituted with a convenient alternative.

Age and liquid resources retain units and monetary basis. Conversion requires an explicit supported rule. A currency or base-year mismatch cannot be solved by relabeling the amount.

## Interests, food and health

Interests and cuisines use controlled catalogs and confirmed selections. General, uncertain and no-fixed-preference states are preserved. Unrecognized text does not become a hidden weight.

Health questions describe coping preferences for the planner. They do not determine illness probability, treatment efficacy or the direct health score. The standard start is an acknowledged scenario, including its health and asset simplifications.

The current questionnaire no longer collects new hard requirements or attribute-preference text. Legacy records retain their original meaning; they are not reinterpreted as current answers.

## Review, confirmation and versions

A review is tied to a specific draft revision and pack identity. Editing the draft invalidates the old review. The server constructs the confirmed profile; a browser cannot submit an arbitrary object claiming to be confirmed.

Old drafts can be read in their original version, but cannot be confirmed under the new questionnaire. Users start a new current draft. The removed migration path must not silently reinterpret old percentage answers as independent ratings. Existing historical profiles and started runs retain their original identities for reading and recovery.

## Compilation and public results

The compiler creates the protocol, stated profile, opening states, catalogs, roster and run configuration. All cities share the same confirmed personal conditions. Local jobs and opportunities come from the pack.

Preflight distinguishes settlement capability, scoring completeness and overall run readiness. A compilable catalog does not guarantee that every user's positively weighted preferences are represented.

Public results preserve L, V, J, the original roster, C/E/M counts, exit reasons, missingness and valuation scope. They are an allowed projection of engine results, not a second implementation of the mathematics.

## Code and checks

| Responsibility | Entry point |
|---|---|
| Shared types | [product_contracts.py](../../src/common/product_contracts.py) |
| Answer handling | [answers.py](../../src/product/answers.py) |
| Input catalog and qualifications | [input_catalog.py](../../src/product/input_catalog.py), [qualifications.py](../../src/product/qualifications.py) |
| Compilation and preflight | [compile.py](../../src/product/compile.py), [preflight.py](../../src/product/preflight.py) |
| Results | [results.py](../../src/product/results.py) |
| Verification | [Ratings](../../tests/product/test_ratings.py), [structured answers](../../tests/product/test_structured_answers.py), [legacy objects](../../tests/product/test_legacy_objects.py) |

See [Shared contract](e3-e5-product-contract.en.md), [product-input assumptions](parameter-assumption-register.en.md#product-input) and [Web setup](../../web/README.md).
