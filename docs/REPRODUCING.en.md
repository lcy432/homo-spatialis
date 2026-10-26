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

<a id="real-models"></a>
## Run with your own model key

After installing the environment above, you can replace the offline planner with Qwen. These commands run a bundled example person through all cities in the selected pack, with four clones per city and three years by default.

### Set up the key and endpoint

Create a pay-as-you-go key in [Alibaba Cloud Model Studio](https://www.alibabacloud.com/help/en/model-studio/get-api-key). If you have no `.env`, copy [`.env.example`](../.env.example) to `.env` in the repository root. Otherwise, edit the existing file. Set:

~~~dotenv
DASHSCOPE_API_KEY=your_dashscope_api_key_here
~~~

Replace the placeholder with your key. You can instead set `DASHSCOPE_API_KEY` in the process environment and remove its line from `.env`. Environment variables are not overwritten; conflicting values in the two places are rejected during model setup. Keep `.env` out of Git. Running the bundled packs needs no BEA key or raw-data download.

The repository currently selects `qwen3.8-flash` through the `product_planner` role and the Beijing endpoint, `https://dashscope.aliyuncs.com/compatible-mode/v1`. Keys, endpoints, and available models vary by region. For a key from another region, use its matching endpoint from the [official regional configuration](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope) and update **both** locations:

| File | Setting |
|---|---|
| [`configs/models.yaml`](../configs/models.yaml) | `product_planner.base_url` |
| [`configs/life/run_product_flash.yaml`](../configs/life/run_product_flash.yaml) | `run.planner.llm.base_url` |

The provider and model must also agree between those two entries. Check that the selected model is available to your account and that `run.paid_budget.price` matches its region and rates. The full-run launcher currently uses a CNY budget; changing the price record to USD alone will fail validation. It does not convert currencies automatically.

### Run a simulation

These commands make paid model requests. Start with the synthetic pack:

~~~sh
uv run --frozen python scripts/run_product_full.py --out runs/first-llm-synthetic --max-cost-cny 20
~~~

Or use the frozen six-city US pack:

~~~sh
uv run --frozen python scripts/run_product_full.py --out runs/first-llm-six-cities --pack configs/product/e3b_us_v1_pack.yaml --abilities edu_at_least_hs --max-cost-cny 20
~~~

The second example explicitly confirms the example person's high-school qualification. It is needed for the starting job and is not inferred from the job title. Both commands still use the bundled person's answers; the [web guide](../web/README.md) explains entering your own profile.

Use a new output directory each time. `--max-cost-cny 20` sets a separate estimated CNY 20 limit for that run; you can lower it. The application reserves the estimated cost before each request and stops when the next reservation would exceed the limit. This uses the price record in the configuration, not a provider-side billing cap. Each new run creates a new budget; limits do not add up across runs. Add `--mock` to either command to check the same pack and input flow without model calls.

### Inspect the result

Paths below are relative to the chosen output directory:

| Path | Contents |
|---|---|
| `summary.json` | Batch status, cities, clones, completed years, and progress |
| `ws/compiled/full/` | Compiled inputs and the effective `run_flash_full.yaml` |
| `ws/archive/full/` | Plans, model-call records, and monthly life history |
| `ws/budget/ledger.sqlite` | Request reservations, recorded usage, and estimated costs |

### Use a real model through the browser

The [local web setup](../web/README.md) uses `service_local_mock.yaml`, which only permits an offline mock. Adding a key does not change that mode. A browser session with a real model uses the production service configuration, an HTTPS site, a server-side model key, and an enabled `live_calls_enabled` switch, with the API and worker running behind the site. See [service operations (Chinese)](design/e3c-service.md) for deployment and invitation setup. The command-line runs above do not need a website or HTTPS deployment.

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
