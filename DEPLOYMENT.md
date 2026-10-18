# Deployment package of the read-only grant showcase (Stage 10C-2)

**Nothing is deployed.** This document describes a deployment package and how it was rehearsed locally. Rehearsed means: run and tested on this computer, on 127.0.0.1 only, through the browser tests. Proposed means: written for a server, but not run anywhere. The showcase design is in [GRANT_DEMO.md](GRANT_DEMO.md).

## 1. Architecture

```
visitor ──HTTPS──> reverse proxy (Caddy, TLS)            one public origin, e.g. https://showcase.example.org
                    ├─ /api/*   ──> backend 127.0.0.1:8000 (uvicorn, 1 worker, READ_ONLY_MODE)   /api prefix removed
                    └─ /*       ──> static frontend build (www/), single-page routing
```

- **One origin.** The frontend is built with `VITE_API_URL=/api`, so the browser never makes cross-origin requests and `CORS_ALLOWED_ORIGINS` stays empty.
- **No development server is public.** The backend listens on loopback only. The frontend is served as static files; `vite preview` is not used.
- **Server-side credentials only.** The access registry holds token digests and is read only by the backend. The frontend build contains no credential; this is checked at build time and in the rehearsal.

| Item | Status |
|---|---|
| `scripts/build_showcase.py`: deployable bundle (data, registry, frontend build, settings) | Implemented, run, tested |
| `deploy/rehearsal_proxy.py`: local stand-in for the reverse proxy | Implemented, tested (rehearsal only, not a production server) |
| Rehearsal: the bundle run as on a server, plus browser tests (`scripts/e2e_run.py`, environment `rehearsal`) | Implemented, tested |
| `deploy/Caddyfile`: TLS, `/api` routing, SPA fallback, headers, caching | **Proposed, not tested with Caddy** (not available) |
| `deploy/rwa-showcase-backend.service`: systemd unit with hardening | **Proposed, not tested with systemd** (not available) |
| Container image | Not written. Docker was not available, so it could not be tested |

## 2. The bundle (`scripts/build_showcase.py`)

```powershell
.venv\Scripts\python.exe scripts\build_showcase.py --with-stellar-demo     # → scratch\showcase-bundle\<timestamp>\
```

**Contents:**
- `deploy/` goes to the server:
  - `data/showcase.db` and `data/storage/`: synthetic records and PDFs;
  - `data/access_control.json`: showcase registry, digests only;
  - `data/anchor_public.json`: a public key, no seed;
  - `www/`: the frontend build;
  - `showcase.env`: the settings;
  - `BUNDLE_MANIFEST.json`: the SHA-256 of every file.
- `evaluator-credentials.txt` is handed to evaluators privately and never deployed.

**Data:**
- The synthetic dataset is seeded through the real API with build-time credentials, which are then discarded.
- The registry holds only two read-only evaluator users (independent legal reviewers; A: e2e-meridian and the demo issuer, B: e2e-harbor) and two institutional keys with the same scopes. Operator and issuer credentials do not exist on the server.
- `--public-showcase` seeding omits the synthetic anchor records: a "pending" record would claim a submission that never happened.
- With `--with-stellar-demo`, the data is added to a copy of the local demo dataset. Its genuine Stellar Testnet anchor (LPDEMO) stays verifiable, because the anchored manifest covers only that asset's own records.

**Refusal checks** (the build stops):
- the frontend build contains a credential, a token digest, `secret_seed`, `sk-ant-` or a private key;
- any deployed file contains a plaintext credential;
- the anchoring account file holds more than a public key;
- the development database or the demo dataset changed.

## 3. Settings of the showcase (`showcase.env`)

| Setting | Value | Why |
|---|---|---|
| `APP_ENV` | `production-showcase` | A publicly reachable server: the production profile and its startup checks (§8) |
| `READ_ONLY_MODE` | `true` | Every write refused with 403 before authentication |
| `API_DOCS_ENABLED` | `false` | No public API documentation |
| `SYNTHETIC_DATA_NOTICE` | `true` | Every page says the data is synthetic |
| `CORS_ALLOWED_ORIGINS` | empty | Same origin through the proxy |
| `CLIENT_RATE_LIMIT_PER_MINUTE` | `300` | Per visitor address |
| `AUTH_FAILURE_LIMIT_PER_MINUTE` | `10` | After 10 failed authentications in a minute, the address gets 429 for the rest of it |
| `MAX_UPLOAD_SIZE_MB` | `0` | Request bodies capped at 64 KiB |
| `AI_PROVIDER` | `disabled` | No AI service is ever called |
| `STELLAR_ANCHOR_SECRET_FILE` | `./data/anchor_public.json` | Verification only: no signing key exists |

**Backend command line** (rehearsed):

```
python -m uvicorn app.main:app --app-dir <repository> --host 127.0.0.1 --port 8000 --workers 1 \
       --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header --no-access-log
```

The working directory is the bundle's `deploy/` directory: the repository's `.env` is never read. `--proxy-headers` makes the rate limits see the visitor's address, not the proxy's. There is no access log, and security events log a hashed address only.

## 4. Local rehearsal (tested)

```powershell
.venv\Scripts\python.exe scripts\e2e_run.py                      # includes the "rehearsal" environment (offline)
.venv\Scripts\python.exe scripts\e2e_run.py --testnet-readonly   # the rehearsal reads Stellar Testnet live (read-only)
```

**What the rehearsal runs:**
- It builds a bundle into a temporary directory.
- It starts the backend with the command line above and the bundle's settings. Only `APP_ENV`'s label differs, to identify the run: `production-e2e-rehearsal` keeps the bundle's production profile, so the production startup checks (§8) run on the bundle's own settings.
- It starts the stand-in proxy, which applies the Caddyfile's rules: `/api` → backend, `/static/*` immutable, everything else `index.html`, the site's CSP and headers, no server banner, 405 for writes to static paths.

**What `frontend/e2e/rehearsal/rehearsal.e2e.ts` checks in Chromium:**
1. **Static serving:** deep links served by the application, never cached; hashed files immutable; a missing hashed file is 404; strict CSP; no server banner; no third-party request.
2. **Browser behaviour:** the browser uses only its own origin and `/api`. A CSP violation would be a console error, which fails the test.
3. **The genuine Testnet anchor:**
   - offline: "Stellar network unavailable";
   - with `--testnet-readonly`: "Commitment verified on Stellar Testnet", confirmed independently on public Horizon (transaction, account, memo digest).

   A synthetic asset of the same showcase shows "Not anchored".
4. **Through the proxy:**
   - writes refused (403) and documentation hidden (404);
   - no anonymous reads;
   - tenant isolation for evaluators and institutional keys.
5. **The frontend build:** no credential and no signing material in any served file.
6. **Failed authentication:** after 10 failures, the address gets 429 with `Retry-After: 60`.

## 5. Dependencies (Stage 10C-2)

**Python** (OSV.dev, installed packages):

| Package | Before | After | Remaining advisories | Exposure on the read-only showcase |
|---|---|---|---|---|
| fastapi / starlette | 0.115.5 / 0.41.3 (7 CVEs) | 0.120.4 / 0.49.3 | 5, fixed only in Starlette 1.x (a major upgrade) | None reached. They concern `StaticFiles`, `FileResponse`, `HTTPEndpoint`, `request.url` host or path, and urlencoded form limits. The app uses none of the first four (verified by search), and forms are only parsed on writes, which are refused before parsing |
| python-multipart | 0.0.18 (7) | 0.0.32 | 0 | — |
| python-dotenv | 1.0.1 (1) | 1.2.4 | 0 | — |
| pypdf | 4.3.1 (49) | unchanged | 49, fixed in 6.x (two majors) | **None on the read-only showcase:** no request parses a PDF (proved by `tests/test_showcase_policies.py`). It parses uploads and re-checks stored files at approval, which matters for any writable deployment. Upgrading can change extracted page text, to which existing approvals are bound, so it needs its own stage |
| pytest | 8.3.4 (1) | unchanged | 1 (tmpdir handling, fixed in 9.x) | Development only |
| pip | 25.1.1 (6) | unchanged | 6 | Tooling of the local virtual environment |

**npm:**
- Every package is at the latest release of its current major; `npm audit fix` without `--force` changes nothing.
- Production bundle: `react-router-dom` 6.30.6, 2 moderate advisories, fixed only in 7.18. One is an open redirect when navigating to a user-controlled path, which the application never does (all targets are built from server identifiers). The other concerns server-side rendering, which is not used.
- Build and test tooling: 13 advisories, among them critical ones in vitest and tinypool (dev and test runners). They are fixed only in majors. The production build uses vite 6.4.4 and esbuild 0.25.12, which are not flagged. The flagged vite 5 and esbuild 0.21 are vitest's own.

## 6. Remaining blockers for a public deployment

1. **Not tested on a real host:** Caddy, TLS and the systemd unit have not run; validate them on the server (`caddy validate`, a staging domain).
2. **Rate limiting:** it is in memory, so a single backend process is required (as configured: `--workers 1`). The proxy should add connection limits too.
3. **Dependency upgrades:** Starlette 1.x, pypdf 6.x, react-router 7 and the test tooling majors, each in a dedicated, fully tested change.
4. **Hosting decisions:** a domain, a host (see GRANT_DEMO.md §7) and who operates and monitors it.
5. **Publishing the evaluator credentials:** decide how the evaluator credentials are handed out, and rotate them by rebuilding the bundle.
6. **The genuine anchor:** it lives in the local demo dataset only. The bundle is reproducible with it only from that dataset (`DEMO.md`).

## 7. Liveness and readiness probes (Stage 10C-3B, T2)

| | `GET /health` (liveness) | `GET /ready` (readiness) |
|---|---|---|
| Question | Is the process up and answering? | Can it safely serve requests now? |
| Checks | Nothing beyond answering | Database `SELECT 1`; the access registry is present and valid; the storage root exists, is a directory, can be listed and, unless `READ_ONLY_MODE`, is writable |
| Answer | 200 with version, environment and the three showcase flags | 200 `{"status": "ready"}` or 503 `{"status": "not_ready"}` |
| On failure | A supervisor may restart the process | A load balancer withholds traffic; no restart needed |

- **Nothing is revealed.** `/ready` is public like `/health`, because probes hold no credential. Its answer names no component, path, issuer or error. The failed components are logged on the server: `readiness.not_ready failed=database:database_file_missing,storage:missing`. The log gives reason codes or exception types only, never an exception message.
- **Nothing is changed.** The probe creates, migrates and writes nothing:
  - a missing SQLite file is reported, never created;
  - the storage root is never created, so it must exist before the server is ready;
  - the registry is the cached one every request uses, parsed again only after the file has changed.
- **Not an integrity check.** The probe verifies no document digest, evidence manifest or Stellar anchor, and contacts no external service. Each component has 3 seconds, and the components are checked concurrently.
- **Probe the backend directly** at `http://127.0.0.1:8000/ready`. The proxy also forwards `/api/ready`, which reveals nothing. With `CLIENT_RATE_LIMIT_PER_MINUTE` set, the probe counts against the probing address, so keep the limit above the probe rate.
- **Limits:** `SELECT 1` proves the database answers, not that its tables are intact. The startup schema check covers that when the server starts. On Windows, `os.access` cannot detect a directory without write permission.

## 8. Deployment profiles and startup checks (Stage 10C-3B, T3)

`APP_ENV` selects a profile: `<profile>` or `<profile>-<label>`. The label (lowercase letters, digits and hyphens) only identifies the server, for example `production-showcase`; `/health` reports the whole value.

| Profile | Meant for | Startup checks |
|---|---|---|
| `development` (default) | This computer, interactive work | None. API docs on, the local frontend origins, no per-client limits |
| `test` | This computer, disposable automated runs (pytest, `scripts/e2e_run.py`) | None |
| `staging` | A private server for a limited audience | Enforced (below) |
| `production` | A publicly reachable server, such as the showcase | Enforced (below) |
| anything else | — | **Refused**, so a typo (`prod`, `Production`, `stage`) never runs as development |

**Rules of `staging` and `production`.** The checks run first at startup, before the registry, the database or the storage is touched. If one fails, the server does not start. `READ_ONLY_MODE` changes none of these rules.

| Setting | `staging` | `production` |
|---|---|---|
| `API_DOCS_ENABLED` | Must be set explicitly. `true` is accepted for a private staging server | Must be `false` |
| `CORS_ALLOWED_ORIGINS` | Must be set explicitly (empty = same-origin only). Each origin must be `https`, or `http` on a loopback host (for a tunnel) | Must be set explicitly (empty = same-origin only). Each origin must be `https` on a non-loopback host |
| `CLIENT_RATE_LIMIT_PER_MINUTE` | 1 to 1000 | 1 to 1000 |
| `AUTH_FAILURE_LIMIT_PER_MINUTE` | 1 to 100 | 1 to 100 |

Wildcard and malformed origins are refused in every profile, as before.

**Why these ceilings.** `ClientRateLimiter` keeps one timestamp per counted event and address, and each request scans that address's last minute. A higher limit therefore costs memory and time per request without restricting anyone. The ceilings are about 3× and 10× the values rehearsed for the showcase (300 and 10). Tokens carry 256 random bits (`secrets.token_urlsafe(32)`), so the failure limit slows scripted guessing and reduces noise rather than being the only protection.

**Limits that need no check.** The passport limit is fixed at 60 requests per minute and institutional client. The AI extraction limit (`EXTRACTION_RATE_LIMIT_PER_HOUR`) cannot be 0. The body size limit cannot be switched off.

**A refusal** is printed by uvicorn ("Application startup failed"), and the process exits with a non-zero code:

```
ERROR rwa_security deployment.configuration_refused profile=production settings=API_DOCS_ENABLED,CLIENT_RATE_LIMIT_PER_MINUTE
app.deployment_checks.UnsafeDeploymentConfiguration: Refusing to start: unsafe configuration for the production profile. Correct these settings (DEPLOYMENT.md, section 8):
  - API_DOCS_ENABLED: must be false in production (the interactive documentation would be public).
  - CLIENT_RATE_LIMIT_PER_MINUTE: must be between 1 and 1000 in staging and production (0 turns the limit off; ...).
ERROR:    Application startup failed. Exiting.
```

It names settings and rules only, never a configured value: no origin (only its position), path, key, token or the unrecognized `APP_ENV` value. Uvicorn's traceback around it shows source-code locations of the application, not configuration.

**Proxy assumptions (unchanged in T3).** The application never reads `X-Forwarded-For` itself. Behind a proxy, uvicorn replaces the client address only for connections from `--forwarded-allow-ips`; uvicorn's default trusts only 127.0.0.1. Never use `--forwarded-allow-ips '*'`: any client could then choose the address the limits count. Without `--proxy-headers`, every visitor shares the proxy's address and its limit. Neither can be checked from inside the application, so both are documented here and in §3.

**Not covered by these checks:**
- `development` and `test` are for this computer only. Nothing stops a public server from being started with them, and the checks cannot see the address uvicorn binds to.
- Running uvicorn with `--lifespan off` skips these checks, together with the existing registry and schema checks.
- File locations (`DATABASE_URL`, `ACCESS_CONTROL_FILE`, `STORAGE_PATH`), the AI provider, `SYNTHETIC_DATA_NOTICE` and `MAX_UPLOAD_SIZE_MB` are not judged.
- Assigned to later tasks of Stage 10C-3B:
  - SQLite hardening (T4); a Horizon verification cache (T5);
  - the frontend build's `VITE_API_URL` guard (T6); backup and restore (T7);
  - a Python lock file (T8); the pypdf and Starlette upgrades (T9, T10);
  - token expiry and rotation (T11); a rehearsal on a real host (T12).
