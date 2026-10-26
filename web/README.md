# Web development and local use

English · [简体中文](README.zh-CN.md)

[Project](../README.md) · [Documentation](../docs/INDEX.en.md) · [Development](../docs/DEVELOPMENT.en.md) · [Web design (Chinese)](../docs/design/e5-web-ui.md)

The browser flow is: private invitation → starting conditions → answers and review → run settings → confirm and start → progress → city comparison → city detail → each clone's annual life.

Preference input uses independent v3.5 ratings from 0 to 100; they do not have to total 100. Review shows both original ratings and service-normalized weights.

The web app reads business data from the E3C service at `/api/v1`. After start confirmation, the service checks and starts the authorized request; the browser displays its reported stage and progress. The local configuration below uses an offline mock and needs no model key. Its outputs test the process rather than real-model performance.

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

For a command-line run with your own key, follow [the real-model guide](../docs/REPRODUCING.en.md#real-models). The local web configuration above only permits mock runs. Browser access to a real model requires the production service with HTTPS, a server-side key, and paid calls enabled; see [service operations (Chinese)](../docs/design/e3c-service.md).

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
- Browser storage holds language, appearance, and one-start recovery records. Recovery stores confirmed-profile and draft IDs, run options, consent identity, acknowledgements, idempotency key, and time. It is cleared after successful acknowledgement, logout, or identity change. Questionnaire and result bodies, invitation secrets, and session credentials are not stored there. See [startRecord.ts](src/draft/startRecord.ts).
- Interface strings live in `src/i18n/zh.ts` and `en.ts`. Business text uses the service's bilingual display envelopes, resolved by object and field location and checked against the source text. Missing translations retain Chinese and are marked untranslated. See [biz.tsx](src/i18n/biz.tsx) and [display/](src/display/).
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
