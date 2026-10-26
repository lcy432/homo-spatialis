# E3B: Commuting data

English · [简体中文](e3b-commute-data.md)

The commuting layer estimates travel between residence and workplace areas. It compiles public observations and explicit assumptions into the engine's route table; it is not a street-level routing service.

## Areas and support

Residence areas are 2020 PUMAs fully inside the metropolitan area and within the supported tax geography. New York residences are limited to its five boroughs; Chicago residences to Illinois. The other four metropolitan areas lie within one state.

Workplace PUMAs are much coarser, often a county or group of counties. Supported workplace areas with at least 5% of incoming employment weight remain separate; smaller areas are pooled. The geography and excluded weights are recorded in the output.

Employment shares use weighted employed residents traveling to those workplaces. They describe existing employment, not vacancies.

## Travel times

For residence area h, workplace area l and mode r:

$$
T_{hlr}
=2\,Q_{0.5}^{(\mathrm{PWGTP})}
\left(\mathrm{JWMNP}\mid h,l,r\right).
$$

This is twice the weighted step median of observed one-way minutes among people who actually use that mode. It preserves an observed value before doubling.

Cells need at least 30 raw observations. Sparse transit and driving cells may borrow from the residence commute tier, then the metropolitan area, but only when the route itself has at least one observation of that mode. Walking/cycling does not use pooled fallback, because borrowing nearby residents' walking times can create implausible long-distance routes.

If no supported mode is known, the engine cannot execute that home–job pairing. It does not assign zero minutes. A separate typical residence-area commute summarizes all destinations and modes for background use.

These estimates reflect self-selection into modes. Pooled cells often understate the time on sparse, longer routes. Neither the survey median nor doubling it reconstructs an individual's door-to-door journey.

## Mode selection and cost

The engine selects the fastest available round trip, breaking time ties by cost. Driving requires an active car-access service. Remote work with no attendance produces no work commute.

Transit costs twice the principal operator's one-way fare per round trip. A supported monthly pass acts as a monthly cap. Daily and weekly caps, distance-based fares and transfer surcharges are not fully represented. The current frozen San Francisco data lack a verified transit fare, so transit is unavailable in that pack.

Driving fuel is estimated as:

$$
P_{\mathrm{fuel,trip}}
=\frac{T_{\mathrm{roundtrip}}}{60}
\frac{25}{23.4}P_{\mathrm{gasoline}}.
$$

25 mph is an assumed effective speed. 23.4 mpg comes from the 2024 FHWA light-duty fleet reference; gasoline prices use the frozen BLS metropolitan annual averages. Monetary amounts use `USD_2024_LOCAL` and are rounded to cents.

The common car-access fee removes fuel from the AAA small-sedan annual cost:

$$
P_{\mathrm{car,month}}
=\frac{8{,}886-15{,}000(0.1112)}{12}
=601.50.
$$

Fuel is then charged only for actual driving commutes. For a 30-minute San Francisco round trip, the frozen fuel estimate is 2.60; twenty trips plus car access cost 653.50. The national car-cost proxy does not reproduce local parking, insurance, tolls or non-work driving.

## Outputs and provenance

Each city file contains residence/work areas, route cells, car access, employment shares, fare evidence, assumptions, omissions and diagnostics. Its content identity covers the output itself.

Raw files must match the intake fetch fingerprints. Fare numbers must be supported by the stored source excerpts. A failed check disables that mode rather than substituting a guessed fare. Source applicability periods and retrieval dates remain distinct concepts even where the current source contract stores one date field.

## Implementation and checks

| Responsibility | Entry point |
|---|---|
| Compilation | [commute.py](../../src/product/realdata/commute.py) |
| Mapping settings and fare evidence | [commute_us_v1.yaml](../../configs/intake/commute_us_v1.yaml) |
| Commands | [life_pack_commute.py](../../scripts/life_pack_commute.py): `fetch-costs`, `check-costs`, `build` |
| Frozen data | [commute](../../data/frozen/life/us-v1/commute/) |
| Verification | [test_commute_data.py](../../tests/product/test_commute_data.py) |
| Execution | [life_commute.py](../../src/econ/life_commute.py) |

Only `fetch-costs` downloads evidence; checks and construction run offline against the cache. Broader coverage and richer travel choices are part of [the roadmap](../../PLAN.en.md).
