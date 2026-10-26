# Web development and local use

English · [简体中文](README.zh-CN.md)

[Project](../README.md) · [Documentation](../docs/INDEX.en.md) · [Development](../docs/DEVELOPMENT.en.md) · [Web design (Chinese)](../docs/design/e5-web-ui.md)

Use the web app to enter your own details, review your profile, run a simulation, and explore the results visually. Start with a private invitation, confirm your starting conditions and run settings, then follow progress. The results let you compare cities and explore each clone's life year by year.

In the current v3.5 questionnaire, each preference is rated independently from 0 to 100; the ratings do not have to total 100. The review page shows both your original ratings and the normalized weights calculated by the service.

The web app gets its data from the E3C service at `/api/v1`. When you confirm a run, the service checks and starts it; the browser displays progress. The local setup uses a mock planner—a deterministic offline planner for testing—and needs no model key. Mock refers to the planner, not to a fixed example profile: you can still enter your own details.

## Run locally without model calls

Use Node.js 22 and the repository's Python environment (`uv sync --locked` from the root). Open four terminals.

1. Start the API from the repository root. It binds only to 127.0.0.1:8765 and uses the offline mock:

   ~~~bash
   uv run python scripts/product_service.py api --config configs/product/service_local_mock.yaml
   ~~~

2. Start the worker from the root:

   ~~~bash
   uv run python scripts/product_service.py worker --config configs/product/service_local_mock.yaml
   ~~~

3. Start the frontend from `web/`. Vite forwards `/api` to port 8765. Its origin, `http://localhost:5173`, matches the service's `site_origin`:

   ~~~bash
   npm ci
   npm run dev
   ~~~

4. Create a private invitation from the root. The secret is displayed once; open it directly in your browser and do not paste it into logs or screenshots:

   ~~~bash
   uv run python scripts/product_service.py invite create --label local-trial --config configs/product/service_local_mock.yaml
   ~~~

The page removes the invitation secret from the address bar immediately. If using `http://127.0.0.1:5173`, change `site_origin` to match rather than allowing multiple origins.

The local configuration selects all six metropolitan areas and four clones per city by default. The current service offers three-year simulations; clone counts can be adjusted within the allowed range before starting. The run-settings preview is authoritative. Runtime depends on workload and hardware. See [service_local_mock.yaml](../configs/product/service_local_mock.yaml).

## Use a real model

To use a real LLM with the profile you enter here, configure the production service with HTTPS, a server-side model key, and paid calls enabled. Follow [service operations (Chinese)](../docs/design/e3c-service.md). The local setup above uses mock.

You can also try a real model through the [command-line guide](../docs/REPRODUCING.en.md#real-models). Those commands use a bundled example profile and do not import profiles from the web app.

## Testing

~~~bash
npm test
npx playwright install chromium
npm run test:e2e
~~~

- `npm test` first checks shared-contract hashes and generated types, then runs unit and component tests.
- `npm run test:e2e` starts an isolated service on 127.0.0.1:8775 and Vite on port 5180. It clears `runs/web-e2e/service` for each run, uses `http://localhost:5180` as the origin, and keeps normal Origin, CSRF, and rate-limit rules. Tests cover the main flow, sessions and identity, draft conflicts, keyboard use, globe rendering, no-WebGL fallback, and 360×740 mobile layouts. Screenshots and accessibility reports go to `runs/web-e2e/`, are overwritten on reruns, and are not committed.
- `npm run typecheck` checks types. `npm run build` verifies contracts, checks types, and creates the build.

Tests use the current checkout by default. To test another backend, set `E5_SERVICE_REPO` and point `E5_CONTRACTS_DIR` to its `contracts/product/v3/`. Keep frontend code, generated types, and backend contracts aligned. Paths resolve relative to the `web/` directory where the command runs.

## Implementation rules

- Generate `src/contract/generated/` with `npm run contract:gen`; do not edit it manually. The source is `src/common/product_contracts.py`, exported to `contracts/product/v3/`. `contract:check` runs before build and test and fails on a mismatch. Missing fields belong in the [interface handoff (Chinese)](../docs/handoffs/e5-bilingual-map-service-requests.md), not in frontend-only extensions.
- Display the service's numerical `display` values. Do not recompute J, averages, rates, or bounds in the browser. Temporary weight previews help users fill the form; the service performs the final conversion. The page does not add city rankings or recommendations.
- Browser storage holds language and appearance preferences, plus records needed to recover an interrupted start request. A recovery record contains confirmed-profile and draft IDs, run options, consent identity, acknowledgements, an idempotency key, and a timestamp. It is cleared after the start request is acknowledged, on logout, or when the user identity changes. Questionnaire answers, results, invitation secrets, and session credentials are not stored there. See [startRecord.ts](src/draft/startRecord.ts).
- Interface strings live in `src/i18n/zh.ts` and `en.ts`. Text returned by the service uses bilingual display envelopes: translations are matched to the relevant object and field, then checked against the source text. Missing translations retain the Chinese text and are marked as untranslated. See [biz.tsx](src/i18n/biz.tsx) and [display/](src/display/).
- Map and globe sources, licenses, and processing are documented in [geographic sources (Chinese)](public/geo/SOURCES.md). Rebuild the basemap with `node scripts/build-basemap.mjs --zh … --en …`.

## Directory guide

| Directory | Contents |
|---|---|
| `scripts/` | Contract generation/checking, shared contract helpers, and country basemap generation |
| `public/geo/` | TopoJSON country maps with Chinese/English boundary conventions, globe texture, provenance |
| `src/contract/` | Generated types, Ajv validation, sample loaders |
| `src/api/` | HTTP client, endpoints, sessions, identity, queries, polling |
| `src/draft/` | Reducer, autosave, editor, review, start-request recovery |
| `src/display/` | Bilingual envelopes, field resolution, name lookup |
| `src/i18n/` | Interface dictionaries and business-text presentation |
| `src/pages/` | Session, home, starting conditions, input steps, run intent, three result levels, runs, settings |
| `src/components/` | Ratings, cities, maps, globe, settings, start, progress, comparisons, annual records |
| `tests/` | Vitest; draft fixtures for intake verification |
| `e2e/` | Playwright, isolated service configuration, startup, scenarios |
