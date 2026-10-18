# Real-browser end-to-end tests (Stages 10B-1 to 10C-2)

Playwright drives **Playwright's Chromium** (full build, new headless mode; Chromium 153 with `@playwright/test` 1.63.0) against the **real backend** (uvicorn, the application as deployed) and a **production build** of the frontend (`vite build` + `vite preview`). The tests use the actual routes, components and API. Nothing in the browser is mocked, except three clearly marked negative tests that make the browser's own request fail (API unreachable, server error, and its recovery).

## Running

Once:

```powershell
cd frontend
npm install                     # installs @playwright/test (exact version, dev dependency)
npm run e2e:install-browser     # Playwright's Chromium, into %LOCALAPPDATA%\ms-playwright
```

Every run, from the repository root:

```powershell
.venv\Scripts\python.exe scripts\e2e_run.py                      # synthetic + demo-offline
.venv\Scripts\python.exe scripts\e2e_run.py --testnet-readonly   # + the live Stellar Testnet check (read-only)
.venv\Scripts\python.exe scripts\e2e_run.py --grep "passport" --headed --keep-temp
```

| Command | Purpose |
|---|---|
| `npm run e2e:typecheck` | Type-checks the E2E code (`frontend/e2e/tsconfig.json`) |
| `npm run e2e` | Raw `playwright test`. It refuses to run without the context that `scripts/e2e_run.py` prepares |

**Exit codes:**
- 0: every environment passed;
- 1: a test or check failed;
- 2: refused (a prerequisite is missing).

With `--grep`, the report-hygiene self-test may not run, so its check is skipped.

## Environments

| Playwright project | Data | Network | Runs |
|---|---|---|---|
| `synthetic` | Seeded by `scripts/e2e_seed.py` through the real API: 2 tenants, 7 assets, synthetic PDFs, random fictional credentials | Offline: outbound HTTP of the backend is blocked | Always |
| `showcase` | The same seed, served as a public read-only showcase would be: `READ_ONLY_MODE=true`, `SYNTHETIC_DATA_NOTICE=true`, `API_DOCS_ENABLED=false` (Stage 10C-1, [GRANT_DEMO.md](GRANT_DEMO.md)) | Offline | Always |
| `rehearsal` | The deployable bundle of `scripts/build_showcase.py` (with the demo dataset's genuine anchor when it exists), run as on a server. The backend runs with the production flags and the bundle's settings, behind `deploy/rehearsal_proxy.py`: the static build, single-page routing and `/api` on one origin (Stage 10C-2, [DEPLOYMENT.md](DEPLOYMENT.md)) | Offline; live Stellar Testnet (read-only) with `--testnet-readonly` | Always |
| `demo-offline` | A copy of the local demo dataset `scratch/stellar-demo/demo` (DEMO.md), with its real Testnet anchor record | Offline | When the dataset exists locally |
| `demo-testnet` | The same copy | Live Stellar Testnet, **read-only** (Horizon reads; no transaction) | Only with `--testnet-readonly` |

The synthetic seed:
- **E2EMHT:** an approved passport.
  - The synthetic prospectus has 7 claims, each with a found quotation, plus an issuer attestation and an independent legal review.
  - A second document, the scanned annex, has an image-only page.
- **E2EDRF:** a draft.
  - One quotation is on the wrong page, one is fabricated, and one claim is unknown.
  - It also has two documents whose stored files were damaged after upload: one removed ("Missing file"), one with bytes appended ("Altered file").
- **E2EREV:** a draft of 7 grounded claims, with no approval. The browser journey reviews and approves it.
- **E2EPND:** an approved passport with a synthetic *pending* anchor record.
- **E2EINV:** an approved passport with a synthetic anchor record whose manifest JSON was tampered with (it no longer hashes to its digest).
- **E2EMNL:** an approved passport of manually entered claims (Stage 10B-3 route, two approvals), for the showcase.
- **E2EHBR:** another tenant's asset.

Synthetic anchor records:
- They were never submitted to any network.
- Their `requested_by` is `e2e-seed-synthetic-record-never-submitted`.
- Their anchoring account is a random public key written to a verification-only file (no seed exists).

The seeded claims come from a scripted list, recorded with the model name `e2e-seed-scripted-claims (no AI model)`, so that they can never pass for an AI result. The interface labels them "Not produced by a supported AI model (test or imported data)".

The seed also writes `manual_journey.pdf` into the run's temporary directory. It is a new 3-page synthetic text PDF, generated per run with the test suite's PDF writer. It is not uploaded by the seed: the manual claim journey uploads it through the browser and quotes it.

## How a run works (`scripts/e2e_run.py`)

1. **Temporary directory per environment.** It holds a new SQLite database, storage and access registry. The working directory of every Python process is that directory, so the repository's `.env` is never loaded, and every setting comes from the run's environment.
2. **Seeding (synthetic)** goes through the real API in a separate interpreter (`scripts/e2e_seed.py`): upload validation, evidence check, approval gates. The demo environments use a copy of the dataset; the original is never written, and the run checks it is unchanged.
3. **Backend:** `uvicorn app.main:app` on a free port chosen by the operating system (never 8000 or 5173). Readiness is the `/health` response naming this run's environment.
4. **Frontend:** `vite build` into the temporary directory (with `VITE_API_URL` = that backend), then `vite preview` on another free port. `frontend/dist` is not touched.
5. **Playwright** runs the environment's project: one worker, no retries, Chromium.
6. **Teardown.** Only the processes started by the run are stopped; nothing else is touched. Then:
   - every report file is scanned for the run's credentials (a file containing one fails the run, and is deleted);
   - the report-hygiene self-test's artifacts are checked (see below);
   - the temporary directory is deleted;
   - the development database is checked byte for byte.

**Safety:**
- `AI_PROVIDER=disabled` with an empty key, so no AI provider is contacted.
- No Stellar signing key is reachable: every environment uses a verification-only account file (a public key, no seed). Anchoring is refused (503 `anchoring_not_configured`), and nothing can be submitted.
- Offline environments set `HTTP(S)_PROXY` of the backend to a closed local port. Horizon is unreachable and is reported as unavailable, never as a success.
- The browser talks to the run's frontend and API only. Anything else (for example the external font stylesheet of `index.html`) is answered locally with an empty response and listed in the report.

## What the tests cover

| Journey | File | What is checked |
|---|---|---|
| A. Authentication | `synthetic/auth.e2e.ts` | Sign-in through the real Credentials panel (labelled inputs). The dashboard reloads in place, and the sidebar shows whom the server says the credential belongs to (name, role, issuer scope), never the credential. Each role sees exactly its tenant's assets. A missing credential and an invalid one ("Credential not accepted") show no data. Credentials stay in the browser tab only |
| B. Asset discovery | `synthetic/assets-documents.e2e.ts` | Metadata, status and issuer key, and the passport link |
| C. Documents | same | Version, file name, size, pages and SHA-256, checked against the backend. Text-layer notice of the scanned page. PDF download through the authorized API, with the downloaded bytes hashed again in the test. A removed or altered stored file is reported and never served. Another tenant gets 404, and no credential gets 403. Source text of exactly the reviewed version, page by page |
| D. Legal claims | `synthetic/claims.e2e.ts` | For every claim: cited page and found quotation, as reported by the backend. The approvals. The seed model is shown as not AI. A draft's unverified and unknown claims are labelled as such, and the approval gate refuses them in the UI |
| Journey (seeded claims) | `synthetic/journey.e2e.ts` | Serial, every step in the browser. See below |
| Manual claim journey | `synthetic/manual-claim.e2e.ts` | Serial: the complete lifecycle on a newly uploaded PDF (Stage 10B-3). See below |
| Manual claims refused | `synthetic/manual-claim-negative.e2e.ts` | The legal reviewer is not offered the form, and the server refuses it (403). Missing, invalid and institutional credentials are refused; another tenant gets 404. Cross-asset and cross-document version references (422). A too-short quotation. An image-only page cannot be chosen, and the server refuses it (`no_text_layer`). A removed or altered stored PDF cannot be quoted (409 `source_integrity_failed`). A server error is shown without internal detail, an unreachable server is reported, and nothing is created |
| E. Stellar anchor | `synthetic/anchor.e2e.ts`, `synthetic/anchor-states.e2e.ts`, `demo-offline/`, `demo-testnet/` | See below |
| F. Legal Passport | `synthetic/passport.e2e.ts` | Every right's badge follows the backend's flags (found evidence is never "independent"). Verification meaning, approval chain by reviewer role (no identities), and the anchor card. Negatives: draft (no passport), another tenant's key, no key |
| G. External AI | `synthetic/ai-and-negative.e2e.ts` | The disabled provider is stated on the page. "Run AI Extraction" is disabled. The API refuses with 503 `ai_provider_disabled` and nothing is created. Seeded extractions are labelled as not AI |
| Negatives | same | Tenant isolation by URL, wrong document or extraction reference, API unreachable, server error and recovery once the API answers again, back and forward navigation. Unknown address: a "Page not found" page with the navigation and no API request; this also applies to an unknown address under an asset |
| Report hygiene | `synthetic/report-hygiene.e2e.ts` | A passing test, and a deliberately failing one (see "Credentials in reports") |
| Deployment rehearsal | `rehearsal/rehearsal.e2e.ts` | Static serving: SPA deep links, immutable hashed files, a missing hashed file is 404, strict CSP, no server banner, no third-party request. The browser uses its own origin and `/api` only, with no CSP violation. The genuine Testnet commitment is verified only when Horizon confirms it (live run), and a synthetic asset is "Not anchored". Through the proxy: writes refused, docs hidden, no anonymous reads, tenant isolation. The frontend build holds no credential. Failed authentications are rate-limited (429) |
| Read-only showcase | `showcase/showcase.e2e.ts` | Every page is labelled (read-only, synthetic, Testnet only, not production), and the explainer is shown. An anonymous visitor gets no data. An evaluator reads the manual-claims passport from document to approvals, with the PDF downloaded and hash-checked. Every write action is absent from the interface. The anchor states are shown as they are. Every write route (11) is refused by the server for every credential (operator, issuer, legal reviewer, none, invalid) and nothing changes. API docs answer 404, and responses carry the security headers |

**The Legal Passport journey on seeded claims** (`journey.e2e.ts`, Stage 10B-2):
1. The platform operator registers asset E2EJRN through the form. The backend has it with the right issuer, and the other tenant gets 404.
2. The issuer reviewer uploads the synthetic prospectus through the upload form. Version 1, file name, size, 4 pages and SHA-256 equal the file and the backend. The downloaded PDF hashes to the same SHA-256.
3. With AI disabled, the AI path creates no claim for that PDF: the action is disabled, the API refuses, and the document has no extraction. "Add Manual Claim" is offered instead; it is exercised in `manual-claim.e2e.ts`.
4. The journey continues on E2EREV, a seeded draft of scripted claims on the same synthetic PDF. Every claim is bound to the document, the version and its cited page, and each "View page" shows the cited page containing the quotation.
5. The issuer reviewer starts the review and gives the issuer attestation.
6. The independent legal reviewer is offered "Add Your Approval" and gives the legal review.
7. The institution reads the passport with its key. Every right equals the approved claim (value, page, quotation, document and version), and the badges and the approval chain match the backend.
8. The anchor tab shows "Not anchored", and nothing is submitted.

**The manual claim journey** (`manual-claim.e2e.ts`, Stage 10B-3), with AI disabled throughout:
1. Registering an asset is refused to the issuer reviewer (403, no `asset:create`). The platform operator registers asset E2EMAN through the form.
2. The issuer reviewer uploads the new synthetic PDF (`manual_journey.pdf`) through the upload form. Version 1, 3 pages and SHA-256 equal the file and the backend, and the downloaded PDF hashes to the same SHA-256.
3. The issuer reviewer opens "Add Manual Claim" on the document. The pages are listed, and the selected page's stored text is shown. The reviewer enters a claim (Jurisdiction) and:
   - a fabricated quotation: refused ("quotation not found in this document version"), and nothing is stored;
   - the real quotation, cited on the wrong page: refused ("not on the cited page", "it appears on page 3"), and nothing is stored;
   - the real quotation on page 3: created.
4. The new record is an unapproved draft labelled "Manual entry · no AI" with the manual disclaimer. Its claim is stored exactly as typed and verified on page 3 of version 1; the other six fields are unknown. It has no approvals, and no passport is served for it.
5. The issuer reviewer starts the review and gives the issuer attestation. A second approval by the same reviewer is refused (`duplicate_approval`). The passport then states the issuer's attestation only.
6. Another tenant's reviewer (404) and an institutional key (403) cannot approve. The independent legal reviewer is offered "Add Your Approval" and gives the legal review. The two approvals have distinct reviewers and roles.
7. The institution reads the passport. The right equals the claim: value, page, quotation, document and version 1, with `value_origin: manual_entry` and the independent attestation. The page shows "Entered manually … (no AI)", the attestation line, "Manual entry (no AI model)" and six unknown rights.
8. The new asset is "Not anchored", and no anchoring request is sent.

**Stellar anchor states:**

| State | Where | What the page shows |
|---|---|---|
| Not anchored | E2EMHT, E2EREV | "Not anchored". The anchoring attempt is refused, and a declined confirmation sends nothing |
| Network unavailable, record pending | E2EPND (offline) | "Stellar network unavailable", recorded status "Pending" marked as the server's record only, and a note that a pending transaction proves nothing until the ledger confirms it. "Refresh pending transaction" changes nothing. The legal reviewer is offered no transaction action (Stage 10B-3), only reading and re-checking, and the server refuses one (403) |
| Record invalid | E2EINV | "Anchor record invalid", without any network check. Its stored "Confirmed" status and equal digest are not presented as proof ("its digest is not relied on") |
| Network unavailable, real record | demo-offline | "Stellar network unavailable", never verified |
| Verified | demo-testnet | Only when public Horizon independently shows the transaction, its account, ledger and memo digest |
| Evidence changed | demo-testnet | After a material change to the asset in the run's copy, the same valid on-chain commitment is shown as "Evidence changed since anchoring" |

"Pending confirmation" (a pending record that Horizon does not yet return) needs a transaction that the live network has not yet included. No such transaction is created here, so that state is covered by the frontend unit tests only.

**Diagnostics:**
- Every test fails on unexpected browser console errors or page errors; expected ones are declared per test.
- Failed requests are attached to the report as method, path and status only, never headers or bodies.

**Credentials in reports**, prevented at the source (Stage 10B-2):
- Traces, videos and the HTML report are off, because they record typed values.
- Playwright's page snapshot in `error-context.md` holds input values. It is turned off with `PLAYWRIGHT_NO_COPY_PROMPT=1`.
- A failed matcher's own page snapshot (`errorContext`) is dropped by the test fixture before anything is written.
- Credential values in a failure message are replaced with `[credential redacted]` before Playwright writes `error-context.md`, the JSON report or the console output. This covers a matcher's "Received" value and the element preview in the call log.
- Terminal colour codes are removed before that replacement (Stage 10B-3). A matcher's diff highlighting can fall inside a value, depending on its characters, and the written file strips the codes, which joins the value again. The self-test found this as an intermittent leak, in one run's `error-context.md` and `report.json`. The runner's scan now also checks each file with colour codes removed (raw and JSON-escaped), and a self-test checks the redaction of a colour-split value.
- Screenshots are taken for failures only (the self-test takes one on purpose) when the test function ends. Credential fields are password inputs and no page renders a credential, so a screenshot shows dots.
- A failing test's inputs and session storage are cleared before its browser context closes.

The self-test fails on purpose with a credential typed into the sidebar, and its failure message names that credential's value. A further test checks the redaction of a colour-split credential without writing anything. The run passes only if all of these hold:
- the self-test wrote its screenshot and `error-context.md`;
- that file carries the redaction marker;
- the scan of every retained file finds no credential.

The scan checks that the prevention worked; it does not replace it.

**Reports:** `scratch/e2e/<run>/<environment>/` (gitignored) contains:
- `report.json`;
- `artifacts/` (failure screenshots and the self-test's artifacts);
- `backend.log`, `frontend.log`, `seed.log`;
- `scratch/e2e/<run>/summary.json`.

## Gaps

Fixed in Stage 10B-2 (found in 10B-1):
- Unknown addresses: there is now a "Page not found" page with the navigation (it was a `test.fixme`).
- The PDF can be downloaded from each document card. It is saved only when its bytes match the recorded SHA-256.
- The page reloads when credentials change, and the sidebar shows the server-confirmed identity.
- "AI extraction is disabled" is stated on the page and the action is disabled, instead of a native `alert()`.
- Every page has its own header and document title, including "Extraction Detail" and "Page Not Found".
- The application has a favicon.
- The Credentials, asset, upload and passport lookup inputs are associated with their labels.
- The legal reviewer can add the second approval in the interface ("Add Your Approval"); before, the form disappeared after the first approval.

Fixed in Stage 10B-3:
- Legal claims can be entered manually for a newly uploaded PDF, without AI. The complete lifecycle (new asset, new PDF, manual claim, source verification, two-reviewer approval, passport, anchor state) is exercised in the browser (`manual-claim.e2e.ts`).
- A document's stored page text can be read, page by page, in the manual claim form, including for documents without an extraction.
- Anchor transaction actions are offered only to roles with `anchor:create`, and a pending record carries a note that it proves nothing until the ledger confirms it.

Still open:
- A manual record's author is recorded in the security log only. Extraction records have no author column; adding one needs a migration.
- The platform operator holds `extraction:run` for every issuer, so it can create manual claims, as it can run AI extraction. It cannot approve them.
- Registering an asset is the operator's role only (`asset:create`); an issuer reviewer cannot register one.
- A manual claim set is created with its claims in one request. The form sends one claim; further fields are added to the draft with the existing correction form.
- The PDF is downloaded, not previewed in the page.
- **Legal claims are reached per extraction (Documents → Review),** not from an asset-level claims section.
- `index.html` loads an external font stylesheet. The tests answer it locally, so they never depend on it.
