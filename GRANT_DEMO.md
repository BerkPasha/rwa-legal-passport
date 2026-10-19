# Grant demo readiness (Stage 10C-1)

This is a local readiness stage. **Nothing is deployed.** The application is not production-ready, and nothing it shows is legal advice.

## 1. What an evaluator should understand

The dashboard's "How to read this application" section states each point in the interface:

| Topic | What the application shows | What it does not establish |
|---|---|---|
| Problem | A tokenized RWA on Stellar carries legal rights defined in off-chain issuer documents | — |
| Legal Passport | Approved legal claims per asset, each with document version, cited page and exact quotation, plus the approvals and the Stellar commitment. Read by institutions with an API key | Legal validity, ownership, enforceability |
| Page-grounded verification | The quotation appears word for word on the cited page of that exact document version (server-side check) | That the claim is legally correct |
| Human review | Claims start as unapproved drafts. Issuer attestation and independent legal review are separate approvals by different reviewers, bound to the exact evidence; any change makes them stale | A legal opinion |
| Stellar Testnet anchoring | "Verified" only when Horizon returns the transaction from the trusted account carrying the manifest digest | Anything on Mainnet; legal approval |
| Experimental | AI extraction is disabled or unverified. Claims are entered manually from the stored page text, or are labelled test data | — |

## 2. Two modes

### A. Public read-only showcase (implemented and tested locally; not deployed)

The server settings (`.env.example`):

| Setting | Effect |
|---|---|
| `READ_ONLY_MODE=true` | Every request other than GET, HEAD and OPTIONS gets 403 `read_only_mode`, before authentication and before any route, whatever the credential. The rule is structural, so it also covers write routes added later. The 11 write routes of the application are pinned in `tests/test_showcase_policies.py` |
| `SYNTHETIC_DATA_NOTICE=true` | The interface labels every page "synthetic and fictional", "Stellar Testnet only" and "not a production service" |
| `API_DOCS_ENABLED=false` | `/docs`, `/redoc` and `/openapi.json` answer 404 |

`GET /health` reports these three flags (and nothing else of the configuration).

**Server-side enforcement:**
- On a read-only server, the interface hides every write action (registration, upload, AI extraction, manual claims, review, correction, approval, anchoring) and says why.
- The server refuses those writes itself.
- The browser tests check both, and check that nothing changed.

**Access:**
- An anonymous visitor sees the explanations, but no data. Every data route still needs a credential.
- Evaluators sign in with a published, fictional, read-only showcase credential (the independent legal reviewer role of the synthetic tenant) and an institutional key for the passport lookup.
- On a read-only server these credentials can only read synthetic data.

**Data:** the synthetic dataset (`scripts/e2e_seed.py`):
- an approved passport of AI-style scripted claims (labelled test data);
- an approved passport of **manually entered** claims (E2EMNL);
- a draft with failing evidence;
- pending and tampered anchor records;
- damaged files;
- a second tenant.

The demo dataset (`DEMO.md`) adds a genuine Stellar Testnet anchor.

### B. Restricted evaluator sandbox (design; local preview only)

**Proposed for a later stage, with your approval:** a separate, authenticated deployment. It is never the showcase server with write access switched on.
- **Data and credentials:** synthetic data only. Per-evaluator credentials are issued privately, with short validity, and revoked after the evaluation.
- **Tenants:** each evaluator gets their own tenant (issuer id), so evaluators never see each other's records.
- **Roles:** asset registration stays an operator role, so the sandbox operator pre-registers an empty asset per evaluator. The evaluator, as issuer reviewer, uploads synthetic PDFs, enters manual claims and reviews. A second evaluator credential (independent legal reviewer) gives the second approval.
- **Restrictions:**
  - AI disabled;
  - no Stellar signing key (anchoring refused; verification read-only);
  - upload limit lowered (e.g. 2 MB, 20 pages);
  - per-user rate limits on writes (a new rate limit; today only AI extraction and passport reads are limited);
  - a banner telling evaluators not to upload real documents.
- **Reset:** the sandbox database and storage are recreated from the seed on a schedule (e.g. nightly) and at the end of the evaluation. Nothing in it is kept.

**Local preview:** `python scripts/grant_demo.py --sandbox` starts a writable, disposable environment of synthetic data on this computer only. Stopping it deletes it, which is the reset procedure.

## 3. Running the local demo

```powershell
.venv\Scripts\python.exe scripts\grant_demo.py                                   # read-only showcase, synthetic data, offline
.venv\Scripts\python.exe scripts\grant_demo.py --dataset stellar-demo            # read-only, the genuine Testnet anchor record, offline
.venv\Scripts\python.exe scripts\grant_demo.py --dataset stellar-demo --testnet-readonly   # the same, verified live (read-only)
.venv\Scripts\python.exe scripts\grant_demo.py --sandbox                         # writable sandbox, synthetic data, local only
```

**What it does:**
- Creates a new temporary database, storage and access registry with fresh fictional credentials, using the browser tests' isolated environments (`E2E_TESTING.md`).
- Serves the real backend and a production build of the frontend on free ports of **127.0.0.1 only**.
- Runs with `AI_PROVIDER=disabled` and no Stellar signing key. The repository's `.env` is never loaded.
- Writes the credentials to `<temporary directory>\demo-credentials.txt` and prints only that file's path.
- Ctrl+C (or `--minutes N`) stops the environment and deletes it, then checks that the development database and the demo dataset are unchanged.

**Suggested walkthrough** (about 10 minutes):
1. Dashboard: "How to read this application" and the showcase banner.
2. Sign in with the evaluator credential. Open E2EMNL, then Documents:
   - version, SHA-256, pages;
   - download (hash-checked);
   - Review: claims labelled "Manual entry · no AI", each quotation found on its cited page; the read-only review panel and the approval history.
3. "Open Legal Passport" with the institutional key. Show:
   - each right's origin;
   - its attestation (issuer only, or also the independent legal reviewer);
   - its source passage and page;
   - the Stellar commitment card ("Not anchored").
4. E2EMHT: the scanned annex's page without text layer. E2EDRF: a draft whose fabricated and wrong-page quotations block approval.
5. Stellar states:
   - E2EINV: invalid record;
   - E2EPND: pending record, network unavailable;
   - with `--dataset stellar-demo --testnet-readonly`: a genuine commitment verified on Stellar Testnet, with links to the transaction on Stellar Expert and Horizon.
6. Optional, with `--sandbox`: the complete lifecycle of `manual-claim.e2e.ts` by hand (new asset, upload, manual claim, refusals, two approvals, passport).

## 4. Security audit (Stage 10C-1)

**Implemented and tested in this stage:**
- **Read-only mode, API docs switch, `/health` flags:** see §2.A.
- **Security headers on every API response, errors included:**
  - `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`;
  - `Cross-Origin-Opener-Policy: same-origin`, a restrictive `Permissions-Policy`;
  - `Cache-Control: no-store`;
  - `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'`. Swagger UI and ReDoc, which load a CDN, carry no CSP.
- **Unhandled errors:** answered with a generic 500 that reveals no detail (tested). Exception: Starlette's 500 response is produced outside the middleware stack, so it carries no security headers. Low risk: its body is generic.

**Already in place (earlier stages):**
- **Access:** a fail-closed access registry holding token digests only; issuer-scoped tenancy (another tenant gets 404); role permissions on every route (route inventory test).
- **Request limits:** CORS limited to configured origins (no wildcard, no cookies); a body size limit before parsing; PDF page, text and time limits.
- **Rate limits:** per-key passport reads, per-user AI extraction.
- **Logging:** security logs without tokens or document text.
- **Configuration:** AI off by default; secrets gitignored; the E2E report redaction and scanning.

**Deployment blockers** (to resolve before any public exposure). Stage 10C-2 addressed items 1 to 3 and 6 to 7 as far as possible without a host; the remaining ones are in [DEPLOYMENT.md](DEPLOYMENT.md) §6:
1. **TLS and hosting:** HTTPS termination and HSTS must be added by the hosting proxy. The local stack has neither.
2. **Static frontend:** the frontend must be served as static files by a web server or CDN. `vite preview` is a development tool; its dev-dependency advisories (see §6) concern it.
3. **Rate limits:** they are in memory per process and reset on restart. A public deployment needs one process, or a shared store, plus a limit on failed authentication attempts per IP (none today; tokens are high-entropy).
4. **Database and storage:** SQLite and local file storage are suitable for a single-instance read-only showcase only. The sandbox needs per-reset recreation, and production needs a managed database.
5. **CORS:** `CORS_ALLOWED_ORIGINS` must be set to the exact public frontend origin.
6. **Credentials:** the showcase needs its own access registry, holding only the published read-only evaluator credential and institutional key. Never reuse development or demo credentials.
7. **Dependency updates:** the npm advisories in §6 and the Python packages still to be audited (pip-audit is not installed).
8. **Extraction authorship:** extraction records have no author column (security log only; needs a migration).

## 5. Stellar evidence integrity

- A commitment is "verified" only after a live Horizon read shows a successful transaction from the configured trusted account, whose memo hash and data entry equal the digest of the current evidence manifest.
- **Every other state is shown as such, never as verified:** a local record alone, a pending record, an unreachable network, a tampered record, or changed evidence.
- **Synthetic records cannot pass as verified:** they carry random transaction hashes, and the anchoring account is a random public key whose secret was never created, so no matching transaction can exist on the ledger.
- **No write path reaches Stellar from a showcase:** read-only mode refuses anchoring before any route, and no signing key is configured. This stage submitted no transaction.
- **Live check:** the browser tests had a live re-check of the demo dataset's genuine Testnet anchor (`--testnet-readonly`), independently on public Horizon (transaction, ledger, account and memo digest). Since Stage 10C-3B the browser tests run only on a disconnected computer, so that check is refused and does not run.

## 6. Dependency audit (2026-10-18)

**npm** (`npm audit`, registry query only):
- production dependencies: 2 moderate (react-router / react-router-dom);
- development tooling: 13 more, including high and critical ones in vite, vitest, tailwindcss and their dependencies;
- fixes are available for all of them. Applying them changes dependency versions, which needs approval.

**Python:** not audited automatically (`pip-audit` would need to be installed, which needs approval). Pinned versions:
- fastapi 0.115.5, starlette 0.41.3, uvicorn 0.32.1, pydantic 2.10.3;
- SQLAlchemy 2.0.36, aiosqlite 0.20.0, pypdf 4.3.1, python-multipart 0.0.18;
- httpx 0.28.0, stellar-sdk 16.1.0, anthropic 1.14.0.

Starlette and pypdf in particular should be checked against current advisories before any public exposure.

## 7. Deployment platform options (proposed; nothing configured)

| Option | Fit | Notes |
|---|---|---|
| A container PaaS (e.g. Fly.io, Render, Railway) | One read-only backend instance and a persistent volume for the SQLite file and the PDFs | Managed TLS; deploy the frontend as a static site on the same provider or a CDN |
| A single small VM (any cloud) behind Caddy or nginx | Full control of headers, TLS and rate limits | More operations work |
| A static host or CDN for the frontend (e.g. Cloudflare Pages, Netlify) + one of the above for the API | Removes `vite preview` from production | Set `VITE_API_URL` and `CORS_ALLOWED_ORIGINS` |

For the showcase, a single read-only instance with a pre-built synthetic database and storage, rebuilt from the seed on each deploy, is enough.
