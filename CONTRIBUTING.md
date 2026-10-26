# Contributing

English · [简体中文](CONTRIBUTING.zh-CN.md)

Contribute a reliable piece of city data, a mapping method, or a validation result. You do not need to cover an entire city or have an institutional affiliation or publication record. Start with an issue describing the city, source, and problem, then submit a focused pull request.

V1.1 will introduce a common submission and validation process for city data; that process is still being built. These requirements apply to contributions you can propose today. Read [data and mapping](docs/DATA.en.md) for the current approach and the [development guide](docs/DEVELOPMENT.en.md) for setup and code locations.

## What to include

- **Code or mapping methods:** explain the problem, reason for the change, inputs and outputs, and an example that demonstrates the result. Changes to environment values, mappings, or evaluation assumptions must also update the [parameter and assumption register](docs/design/parameter-assumption-register.en.md). Follow the [project rules (Chinese)](CLAUDE.md) when changing economic formulas.
- **City data:** supply the provenance record below. Prefer acquisition scripts, small public samples, and transformation rules. Raw downloads are not included in the repository by default.
- **Validation or failure cases:** record the software commit or version, data pack and configuration, seeds, planner and actual model version, and expected and observed results. Report mock runs, synthetic experiments, and real-world observations separately. Retain failures and unknowns.
- **Documentation or translations:** identify the implementation and data version described. Distinguish planned capabilities from implemented behavior.

For city data, include this minimum provenance record in the PR description. On merge, it belongs in the relevant configuration or source manifest.

| Field | What to record |
|---|---|
| Contributor | Public name or account, and type of contribution |
| City and geography | City identifier and boundaries: city proper, metropolitan area, state, or another defined area; do not mix them |
| Publisher and source | Dataset, table, fields, original URL, publication date or version, and access date |
| License and redistribution | License name and terms, permission to redistribute or adapt, and attribution requirements; “publicly available online” is not a license |
| Measurement basis | Units, currency, base year, tax treatment, monthly or annual frequency, and population measured |
| Transformation and mapping | Source-to-engine field rules, code or configuration locations, proxies, and imputation assumptions |
| Gaps and limitations | Missing fields, coverage, and known biases; do not replace unknowns with zero or LLM-generated values |
| Verification | A traceable source record, a worked transformation, and check commands and results |

Examples are available in the [US source manifest](configs/intake/us_v1.yaml) and [frozen source report](data/frozen/intake/us-v1/reports/sources.json). These document the checks performed; they do not place every third-party source under Apache-2.0. For licenses such as ODbL, follow the source's requirements for attribution, database sharing, and redistribution. If redistribution rights are unclear, contribute the source reference and method first and discuss whether the data can be included.

## Licensing and provenance

By contributing original code, documentation, or configuration, you confirm that you have the right to submit it and agree to its inclusion under **Apache-2.0**. You retain your copyright. Ordinary contributions do not require a copyright assignment.

Identify the original rights holders and licenses of third-party data, code, and assets, and retain required notices. Do not relabel them as Apache-2.0 without authorization. Original data needs an explicit, separate data license and maintainer review before inclusion. Do not submit credentials, private material, real users' private profiles, or unauthorized personal information.

Please confirm in your PR:

- I have the right to submit this material. Original code, documentation, and configuration are submitted under Apache-2.0; third-party material and data have their sources and licenses listed separately.
- I distinguish observations, proxy mappings, and synthetic assumptions, and retain missing-data and failure records.
- I provide relevant validation and my preferred public attribution.

## Checks and attribution

Installation and the smallest runnable example are in the [reproduction guide](docs/REPRODUCING.en.md). For code changes, run the project's offline checks. Obtain agreement before making paid model calls.

~~~text
uv run --frozen pytest -n auto
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
~~~

For web changes, also follow the [web guide](web/README.en.md). On merge, update the contribution record in [authors and attribution](AUTHORS.en.md) and the relevant city-data provenance. Confirm the software author list for each release.

We ask researchers to [cite the software](CITATION.cff) and the data sources they use. This is a request for scholarly attribution, not an additional license restriction or a guarantee of citation. Apache-2.0's license, copyright, and notice requirements still apply.
