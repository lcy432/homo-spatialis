# Food plans, equipment and hobbies

English · [简体中文](e1-8-life-extension.md)

These contracts connect consumption and participation to the same executed facts used by the money ledger, resource ledger and scorer. They support the current life model and the real-data compiler.

## Facts and validation

| Contract | Meaning and required checks |
|---|---|
| `FoodPlanFacts` | A complete monthly food plan, weekly meal composition, fresh meals, cuisine, cooking and dining time |
| `EnjoymentProvision` | A service function supplied passively while in use or in explicit service units |
| `EquipmentFacts` | Capabilities supplied by a durable item |
| `ActivityFacts` | Primary category, participation and preparation/travel time, required and bundled capabilities |
| `ActionItem.months` | Strictly increasing requested months, including nonconsecutive months |
| `ParticipationRecord` | Actual completion, pause or unavailability for each requested activity month |

Food components must explicitly distinguish zero fresh meals from an unknown count. Cuisine can be unknown, but cannot disappear silently. Cooking plus dining time must agree with the resource facts.

Participation plus preparation/travel time must equal the activity's resource time. Meal replacement is explicitly zero in the supported contract; an activity cannot silently substitute for a complete food plan.

## Execution and equipment

Capability-dependent activities execute after that month's one-time purchases. A plan listing guitar practice before the guitar purchase therefore has the same result as the reverse ordering.

Only an active holding provides its capability. A failed purchase, expired item, future activation or illness cancellation cannot support practice. If the activity cannot occur, it produces no fee, participation hours or benefit. Paused activities also have no actual participation.

Service life decreases by months of service and continues across years. Replacement after expiry is possible; overlapping holdings of the same item are not created to bypass the ordinary purchase rules.

A bundle can provide equipment for that activity alone. It does not create a durable asset. Equipment ownership alone does not count as leisure.

## Scoring

Food uses actual fresh meals and matches to confirmed cuisine preferences. Leisure uses actual participation hours h and primary-category matches:

$$
z_{\mathrm{leisure}}
=b+\Delta_{\mathrm{other}}
+\sum_a h_a(1+0.5\,I_{\mathrm{match},a}),
\qquad
s=\min\!\left(c,\frac{z_{\mathrm{leisure}}}{z_{\mathrm{leisure}}+12}\right).
$$

Preparation and travel consume time but do not enter h. Secondary tags do not multiply the match bonus. Splitting the same participation across records does not create additional hours or reward.

The current real-data pack uses a common leisure background of 1. Current personal consumption uses the standard basket in [A/B mappings](e3b-mapping-and-daily-support.en.md#personal-consumption-basket); the separate comfort-item and massage examples in the original design are historical.

Coverage and scoring share the same fact-gap checks. A gap in an unused catalog option does not necessarily make the realized trajectory incomplete. Known nonparticipation, unknown evidence and unsupported concepts remain distinct.

## Terminal valuation

Reference plans use capabilities of retained equipment even if that model is no longer sold. Where the declared template uses music practice, a remaining service life of r months supports those months; the basic branch then stops and the more comfortable branch can replace the item under its declared rule.

If the person has no relevant capability, the template does not automatically buy equipment and invent an activity. The public valuation-scope report identifies uses absent from the reference set. An unrepresented use is not a claim that the item has no value, nor does it alone turn a clone into a missing simulation.

## Personal input and information

The current questionnaire uses confirmed interest and cuisine selections. Unanswered, uncertain, no-fixed-interest and confirmed states remain distinguishable. Unsupported brand or descriptive text is retained without inventing a recognized preference.

The planner can see plan composition, ordinary hours, capabilities and confirmed interests. Score coefficients, weighted participation, match results and reference plans remain internal. Compact request tables preserve permitted facts; compression must not change available choices or their meaning.

## Code and checks

- [Contracts](../../src/common/life_contracts.py), [settlement](../../src/econ/life_settlement.py), [scoring](../../src/econ/life_scoring.py).
- [Coverage](../../src/econ/life_coverage.py), [terminal valuation](../../src/econ/life_terminal_value.py), [observations](../../src/agentlayer/life_views.py).
- [Extension tests](../../tests/life/test_life_extension.py), [boundary tests](../../tests/life/test_life_extension_closeout.py), [interest tests](../../tests/product/test_interests.py), [valuation-scope tests](../../tests/product/test_valuation_scope.py).
