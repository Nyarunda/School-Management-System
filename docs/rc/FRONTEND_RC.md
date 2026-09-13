# Frontend Release Candidate — Evidence

**Baseline:** `c388659` (`feature/payments` — immediately after Backend RC closed, see `docs/rc/BACKEND_RC.md`). Frontend production image `965b72d3a7d4`, built fresh from this commit.

Same discipline as Backend RC: 9 acceptance areas, each closed with recorded, reproducible evidence — not "the UI looks fine." Every finding is classified **BLOCKER** (cannot release), **DEFECT** (fix before RC approval), **ACCEPTED RISK** (understood and explicitly accepted), or **POST-GO-LIVE** (intentionally deferred). Areas themselves close as **PASS** once every check in them is accounted for.

**Checklist, in approved execution order:**

| # | Area | Status |
|---|---|---|
| 1 | Production build & runtime | ✅ PASS |
| 2 | Authentication & tenant switching | Not started |
| 3 | Authorization/entitlement visibility | Not started |
| 4 | Critical 360 workspaces & journeys | Not started |
| 5 | Finance/M-Pesa UX | Not started |
| 6 | Error/loading/empty states | Not started |
| 7 | Performance | Not started |
| 8 | Responsive/accessibility behavior | Not started |
| 9 | Backend-contract reconciliation | Not started |

## Policy — backend defects discovered during Frontend RC

A backend change is allowed during Frontend RC **only** when it corrects an existing backend capability required by an already-approved frontend/business journey. It must be treated as an RC defect: focused regression tests, pass the relevant backend suite, documented, merged into the release candidate. **No new business capability.** Contract correctness (Area 9) is checked continuously as each area runs, not deferred to the end — if an area's own journey surfaces a gap, it's classified and (if in scope under this rule) fixed there. Area 9 itself is the final reconciliation: proving every ledger entry in `docs/frontend-backend-contract-gaps.md` ended in one of three states — resolved, explicitly accepted/deferred, or obsolete/not reproducible.

## Evidence convention

PASS requires reproducible evidence, not a description of expected behavior:
- **Screenshot** for visual/state behavior.
- **Network trace** for API behavior.
- **Console/build output** for runtime/build behavior.
- **Exact role/tenant/module state** for authorization tests.
- **Before/after DB/API evidence** where a UI action changes business state.

## Area 1 — Production build & runtime ✅ DONE

**Claim under test:** the actual nginx-served production build (not the Vite dev server) boots against the real backend, serves the genuine production bundle, and behaves correctly on hard refresh/deep navigation — with the browser provably hitting the real thing, not an orphaned dev process. This area's evidence bar exists specifically because of a known failure mode encountered earlier in this engagement (an orphaned host `npm run dev` process shadowing the Docker frontend container's port).

### Check 1 — No orphaned dev-server process contamination

**Method:** `Get-NetTCPConnection -State Listen` for ports 5173/5174/3000/4173, cross-referenced against `Get-CimInstance Win32_Process` for the owning PID's command line.

**Found, live, before any other check ran:** port 5173 (the Docker-mapped frontend port) was correctly held only by legitimate Docker/WSL2 processes (`com.docker.backend.exe`, `wslrelay.exe`) — clean. But **two more orphaned host-level Vite dev-server processes**, for this exact project, were found listening on port 5174 (`node .../frontend/node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5174`), both running continuously since **2026-09-10** — three days stale, from before this RC even started. Same failure class as the earlier incident, a different port.

**Action:** killed both (`Stop-Process -Force`), confirmed port 5174 freed. This isn't an application defect — nothing in the frontend build or Docker config caused it, it was leftover host-process hygiene — but it's exactly the kind of contamination this area exists to rule out before trusting any other evidence, so it's recorded rather than silently cleaned up.

**Classification:** operational finding, not a code defect. Resolved.

### Check 2 — Production image builds and boots clean

**Method:** `docker compose up -d --build frontend` (fresh build from `c388659`, no cache reuse of a stale image), then inspected `docker compose ps`/`docker compose logs frontend`.

**Observed:** clean multi-stage build (Node 22 build stage → nginx:alpine runtime), zero build warnings/errors. nginx boots in under 1s, `epoll` event method, 8 worker processes, config validated (`/etc/nginx/conf.d/default.conf differs from the packaged version` — expected, that's our own `nginx.conf` template being applied, not an error). `/` returns `200` on the very first poll after boot.

### Check 3 — Genuinely the production build, not dev artifacts

**Method:** `curl` the served `index.html` and its referenced JS bundle directly; grep for dev-only markers.

**Observed:** `index.html` references hashed, content-addressed assets (`/assets/index-DHfhbhm9.js`, `/assets/index-_u_8A8MF.css`) with `type="module" crossorigin` and `modulepreload` hints — the real Vite production output shape, not a dev-server-served unbundled tree. The JS bundle itself is minified and chunk-split (`__vite__mapDeps` present, referencing dozens of separately-hashed lazy chunks — e.g. `finance-BpYy4yqQ.js`, `mpesa-IRWxPo-k.js`). Grepped the bundle for `@vite/client`, `react-refresh`, `import.meta.hot` — **zero matches**, confirming no dev-mode code path is present.

### Check 4 — Backend/API routing through nginx

**Method:** direct `curl` to `/api/v1/session/` through the frontend origin (`:5173`, not `:8000`), plus a real browser-driven login form submission (Playwright — see Check 6) to prove the round trip works from an actual UI action, not just a synthetic request.

**Observed:** `curl http://localhost:5173/api/v1/session/` → `401` (correctly proxied to the backend, not a static 404 — nginx's `/api/` → `backend:8000` routing, including the DNS-caching fix from Backend RC Area 7, is intact). The Playwright-driven login attempt (real form fill + real click) produced a real `POST /api/v1/auth/login/` → `400`, and the backend's actual validation message (`non_field_errors: Unable to log in with provided credentials.`) rendered correctly in the UI as a toast — full round trip proven, not assumed.

### Check 5 — Hard refresh / deep-route behavior (SPA fallback)

**Method:** Playwright, two scenarios: (a) a fresh browser context navigating **directly** to a deep application route (`/finance/invoices`) — never client-side routed, a true hard entry; (b) `page.reload()` on the already-loaded login page.

**Observed:** (a) direct navigation to `/finance/invoices` returns `200`, the SPA shell boots, `#root` mounts with the same child count as a normal load (3), and — since the session is unauthenticated — the app's own client-side auth gate correctly redirects to the login page rather than rendering a blank screen, a raw 404, or a stuck loading state. Screenshot confirms a fully-rendered login page, indistinguishable from a normal `/` load. (b) reload on the login page re-renders cleanly, same result. Both prove nginx's `try_files $uri $uri/ /index.html;` fallback (fixed/verified during Backend RC Area 7 for a different reason — backend DNS caching — but never previously proven for this specific SPA-routing purpose) works correctly for real deep links, not just the root path.

### Check 6 — Production console/network cleanliness

**Method:** Playwright-driven session across all of the above (fresh load, deep route, reload, real login attempt), with listeners on `console`, `pageerror`, `requestfailed`, and non-2xx `response` events for the entire run.

**Observed:** **zero uncaught page errors, zero network-level request failures.** Exactly one console message and one non-2xx HTTP response were recorded for the entire run — both are the *expected* result of the deliberate wrong-credentials login test in Check 4 (`400` on `/api/v1/auth/login/`, and the browser's own automatic "Failed to load resource: 400" console log for that same request) — not an application defect. No React error-boundary trip, no missing-chunk error, no CSP/mixed-content warning.

**Evidence artifacts:** `docs/rc/evidence/frontend-area1-c388659/` — `01-login.png`, `02-deep-route-unauthenticated.png`, `03-post-reload.png`, `04-login-attempt-result.png` (screenshots), `console-messages.json`, `page-errors.json`, `failed-requests.json`, `non-2xx-responses.json`.

**Classification:** **PASS**. The production frontend, built and served exactly as it would ship, boots clean, serves the genuine production bundle, correctly proxies to the backend (including a real end-to-end form submission), handles hard refresh and deep-link navigation correctly via SPA fallback, and produces zero unexpected console/network errors across the whole check. One operational finding (stale host Vite processes) found and resolved, not a code defect. No application code changed this area.
