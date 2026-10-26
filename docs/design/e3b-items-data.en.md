# E3B: Daily-life opportunities and careers

English · [简体中文](e3b-items-data.md)

This layer turns public records and declared assumptions into executable food plans, household services, equipment, activities, jobs and courses. Records of actual products and venues are distinguished from estimated prices and modeled effects.

The current food grid, support services, consumption basket and wage supports are described in [A/B mappings](e3b-mapping-and-daily-support.en.md). They supersede the early examples retained in the Chinese edition.

## Food and household services

Ingredient prices come from the intake layer. USDA SR Legacy energy values complete the reference baskets against a common calorie target. Source weights may include inedible portions, so this construction can understate the needed addition. ATUS figures provide a time-use magnitude check, not individual cooking-time estimates.

Service prices use a labor-cost proxy:

$$
P_{\mathrm{service}}
=\frac{W_{\mathrm{occupation}}}{2080}
h_{\mathrm{worker}}\times 2.
$$

The overhead multiplier and worker hours are declared assumptions. For laundry pickup and cleaning, the opportunity separately states the person's residual household work. Paying for six worker-hours does not necessarily save six personal hours.

## Appliances and equipment

ENERGY STAR provides model and certification facts, not retail prices, useful life or household time saved. Representative models are selected within category/type by median annual electricity use. Category price assumptions are adjusted using the goods price factor.

The current appliance arrangements include washing machines and washer/dryer combinations. Standalone dryers are not treated as complete laundry substitutes. Dishwashers are excluded because dishwashing is already included in food preparation and cleanup, and the engine has no separate replaceable task for it.

Installation is assumed possible because the housing data lack hookups and ventilation facts; that gap is recorded. The declared lifetimes and `none_within_horizon` failure settings are assumptions, not failure-rate observations. Warranty periods are not failure distributions.

A tennis racket or guitar grants a capability. Ownership alone provides no leisure participation or enjoyment points. See [Life extensions](e1-8-life-extension.en.md).

## Activities

Model activity prices combine labor, venue and materials:

$$
P_{\mathrm{activity}}
=\frac{w_{\mathrm{teacher}}h_{\mathrm{teacher}}}{n_{\mathrm{participants}}}\,2
+P_{\mathrm{venue}}r_{\mathrm{services}}
+P_{\mathrm{materials}}r_{\mathrm{goods}}.
$$

OSM supplies evidence of a suitable venue. A generated price is labeled a model plan, not a quote from that venue. Public pool checks distinguish public access from private pools. Museum admission remains unknown where no supported price exists.

Chicago Park District programs retain their actual dates, age restrictions, enrollment terms and fees. Per-session equivalents divide the term fee by the number of sessions after calendar exceptions. These programs remain non-executable where the engine cannot express their restrictions; they are not converted into activities available every month.

Free exercise and home practice can dominate paid activities under the current hours-and-interest scoring rule. The model does not add a price-based reward to prevent that result.

## Occupations, qualifications and pay

The pack includes 40 SOC occupations with compatible wage and entry-requirement evidence. Occupations requiring unsupported state licenses, or incompatible annual/hourly wage bases, remain outside scope.

Qualifications have three structured sources:

| Qualification | Mapping |
|---|---|
| Education | Confirmed highest level grants that level and lower levels |
| Occupational certificate | Explicitly confirmed possession or completion of a mapped program |
| Related experience | Confirmed occupation and duration meeting the declared related-experience rule |

A job title does not grant a degree, license or certificate. BLS typical entry education is treated as a requirement; that is a modeling choice which may exclude people entering through alternative routes.

Current job candidates use twenty wage supports over P10–P90 and an ACS hours/workplace joint distribution. Pay scales with weekly hours/40; monthly hours use weekly hours × 52/12. The work arrangement records attendance separately. Employers are not identifiable from occupational aggregates.

Starting jobs use the person's confirmed occupation at median pay. An unsupported occupation is reported as unsupported, not silently replaced with another.

## Training

IPEDS program fees and the CIP–SOC crosswalk provide program and related-occupation evidence. A crosswalk alone does not establish qualification or hiring. The current compiler accepts the declared certificate mappings and whole-year course durations; weekly participation uses the stated common assumption.

Start dates are unknown, so the current model assumes courses may begin in any month. Under the current eligibility and data requirements, three programs are included in each of New York and Houston, and none in the other four cities. This reflects the mapping and source coverage, not an absence of training in those cities.

## Outputs, code and tests

Each opportunity retains source records, assumptions, gaps and one compilation status. Known supply, data sufficiency and engine support remain separate.

| Responsibility | Entry point |
|---|---|
| Frozen input slicing | [life_inputs.py](../../src/product/realdata/life_inputs.py) |
| Food, services, equipment and activities | [life_items.py](../../src/product/realdata/life_items.py) |
| Qualifications, jobs and training | [occupations.py](../../src/product/realdata/occupations.py) |
| Checks and freezing | [life_freeze.py](../../src/product/realdata/life_freeze.py) |
| Commands | [life_pack_items.py](../../scripts/life_pack_items.py): `fetch`, `slice`, `build`, `start-job` |
| Verification | [Item tests](../../tests/product/test_life_items_real.py), [occupation tests](../../tests/product/test_occupations_real.py) |

Frozen output is in [items](../../data/frozen/life/us-v1/items/). `fetch` is the explicit network step; slicing and construction use cached evidence. Assumptions are indexed in the [parameter register](parameter-assumption-register.en.md#mapping).
