# Roadmap

English · [简体中文](PLAN.md)

This document sets the product direction and release sequence. Module status and default entry points are maintained in the [project index](docs/INDEX.en.md). Both READMEs summarize this roadmap.

## V1: release the foundation

Package personal input, city data, annual planning, monthly settlement, and result viewing into software people can install, run, and inspect. Release preparation includes bilingual documentation, stable offline examples, web/service integration, and verification of actual runs.

See the [methods report](docs/METHODS.en.md) and [reproduction guide](docs/REPRODUCING.en.md). Release records, engineering checks, and simulation findings are separate records; a version number is not an experimental result.

## V1.1: contribute a city

Establish a common city-data format and submission and validation process so contributors can add places they know or want to explore.

- Specify geography, source, date, units, currency, and base year.
- Distinguish observations, derived values, model assumptions, and missing data. Include examples of source-to-engine transformations.
- Validate and trial-run packs before adding them to the available cities.

You can already propose sources and mappings through the [contribution guide](CONTRIBUTING.md). The common intake process is still being built.

## V2: more detail in everyday life

Improve the mathematical models and data mappings for commuting, leisure and hobbies, and everyday consumption. Focus on how these activities use time and money, the opportunities and constraints people face, and how current simplifications affect different people.

## V3: learn through interviews

Add interview-based input so people can describe themselves through experiences, priorities, and trade-offs. Organize and clarify their statements, then obtain confirmation before using them in a simulation. Study what interviews and structured questionnaires each contribute.

See the [interview and preference literature](docs/REFERENCES.en.md#interviews).

## Longer term: international comparison

Bring cities across countries and regions into a shared comparison framework. Beyond data coverage, this requires handling local wages and taxes, consumption and service differences, currencies and price years, occupations and qualifications, and cross-border migration conditions. This goal has no committed release date.

## Research across releases

Study whether models follow individual preferences, how the costs of mistakes differ across groups, and how real-world data can calibrate simulation rules and mappings. Search, optimization, and learning are candidate approaches; their effectiveness needs independent validation. The [research guide](docs/RESEARCH.en.md) describes existing static measurement tools and planned calibration and validation.

Earlier engineering plans remain in the [pre-split archive (Chinese)](docs/history/pre-readme-split-2026-10-26/PLAN.md); older section references are explained in the [history guide (Chinese)](docs/history/README.md).
