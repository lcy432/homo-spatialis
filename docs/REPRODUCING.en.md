# Reproducing the first simulation engine

English · [简体中文](REPRODUCING.md)

This guide runs a complete product flow using the bundled synthetic people, synthetic cities, and deterministic mock planner. It checks engineering behavior; it does not validate real city rankings or real LLM performance. The software version is in `pyproject.toml`. The initial release target is `0.1.0`; see the [project index](INDEX.en.md) for status.

## Environment and source

You need Git, [uv](https://docs.astral.sh/uv/getting-started/installation/), and Python. The repository's `.python-version` specifies Python 3.13; `uv.lock` pins dependencies. Initial installation needs network access. The two simulation commands below need no model key and contact neither model nor external-data endpoints.

~~~text
git clone https://github.com/lcy432/homo-spatialis.git
cd homo-spatialis
uv sync --locked
~~~

Once the release exists, run `git checkout v0.1.0` before installing. Until then, use the release-preparation commit and record `git rev-parse HEAD`; a moving `main` is not a fixed version. A release source ZIP can also be extracted and used with the same commands.

The application runs from source rather than requiring installation as an importable package of the same name. This example needs no `.env`, web server, raw city-data download, or `make check-env`. Keep the `run-offline` subcommand shown below: the separate `run_product_full.py` entry point requests paid models unless `--mock` is supplied.

## Successful example

~~~text
uv run --frozen python scripts/product_e3a.py run-offline --out runs/reproduce-v0.1.0
~~~

The output directory must not exist. Choose a new name when repeating the run. The flow is: draft answers → review and confirmation → compilation → preflight → idempotent submission → mock annual planning and monthly settlement → evaluation and product result.

Inputs mainly come from `configs/product/e3a_synthetic_pack.yaml`, `configs/life/*_product.yaml`, and `src/product/fixtures.py`. Geography identifiers `US:SYNTH2026:K51` and `US:SYNTH2026:M52` refer to fictional cities.

Expected outputs:

| File | What to check |
|---|---|
| `runs/reproduce-v0.1.0/summary.json` | `status: completed`; two completed clones, three years each; `planner_calls: 6`; `real_model_calls: 0`; `provider_calls_recorded: false` |
| `messages/result.json` | Evaluations, population denominators, limitations, and provenance for two synthetic cities |
| `messages/receipt.json` and `messages/duplicate_receipt.json` | The same run identifier; duplicate submission did not create a second simulation |
| `workspace/compiled/normal/` and `workspace/archive/normal/` | Compiled configuration, input snapshots, and execution archives |

Save the command, source commit, and `summary.json`. Reproduction compares business results under fixed inputs; paths and execution times need not be byte-identical. The sample scores are not a real-world welfare scale or evidence that one real city is better.

## Rejected-input examples

~~~text
uv run --frozen python scripts/product_e3a.py reject-example --out runs/reproduce-v0.1.0-rejections
~~~

Inspect `rejections.json`. A device with a known price but unknown lifetime stays outside the executable catalog (`device_in_e1_catalog: false`). An occupation with no matching local job produces a preflight issue. Missing currency/base-year conversion produces `money_conversion_missing`. These are expected rejections of controlled inputs, not estimates of real-world incidence.

## Automated checks

~~~text
uv run --frozen pytest -n auto
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
~~~

Default tests exclude `live` endpoints and `slow` research tests. Passing them does not mean every research experiment has been completed. Record the actual test count, platform, and result. See [module status](INDEX.en.md#status) and the [development guide](DEVELOPMENT.en.md). Linux CI must be verified from its actual GitHub run; Windows results do not establish it.

## Scope of reproduction

- The stable reproduction entry point is the Python offline flow above. The web app and hosted service remain under development; the initial release does not promise a complete deployment experience.
- Six US metropolitan-area configurations and exports are bundled, with limits in coverage, geography, proxy mappings, and synthetic supplements. The default example does not validate them as real-world predictions.
- Pinned dependencies are not an offline distribution of every dependency. Initial installation requires reachable package sources. Platform support is established by recorded runs.
- Real model services can change or become unavailable. Rolling model versions, sampling, and provider differences limit repeatability. Record the actual returned model version, configuration, seeds, costs, and failures.
- For research, preserve the software commit/version, data pack and content identity, configuration, planner, outputs, and failures. Cite software and data separately.
