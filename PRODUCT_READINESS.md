# Product Readiness Audit — Stage 9A

**Project:** RWA Legal Passport — Institutional Legal Transparency API
**Audit date:** 2026-10-15
**Commit audited:** `07ad72f` (working tree clean; no application source was modified)
**Scope:** FastAPI backend, SQLite/SQLAlchemy models, React/Vite/TypeScript frontend, all existing tests.

> **Bottom line:** the core ideas hold up when the system runs against mocks. AI output is always `draft`, every response carries a disclaimer, approval needs non-empty evidence, approved extractions are frozen, and the passport serves only approved data. The product is **not ready for external or institutional use**. Sixteen of 22 API routes have no authentication, including the routes that change data shown in the "authoritative" passport. An unauthenticated upload can overwrite an approved source PDF. A new document version or an asset metadata change does not invalidate an existing approval. "Verified" source passages are never checked against the document text. The real AI path has never run.

---

## 1. How this audit was performed

| Activity | What was done | Result |
|---|---|---|
| Code review | Read every file under `app/`, `tests/`, `frontend/src/`, plus config, `.env.example`, `.gitignore`, `start-rwa.bat` | — |
| Backend tests | `.venv\Scripts\python.exe -m pytest -v` | **86 passed, 0 failed** (3.8 s) |
| Frontend tests | `npx vitest run` | **25 passed, 0 failed** (3 files) |
| Frontend type-check | `npx tsc --noEmit` | **exit 0** |
| Behavioural probes | 18 sandboxed probes (§6) run against the real app in-process: in-memory SQLite, the project's own `MockAIService` / `MockStellarService`, fictional test credentials, storage inside a temp scratch folder | Results in §6 |
| Secrets scan | Compared `.env` secret **values** (never printed) against git history, tracked source, `frontend/dist`, README | No secret value found anywhere |
| Local DB | Read-only (`mode=ro`) row counts of `rwa_legal_passport.db` | All 8 tables empty; `./storage` does not exist |

**Not done, by design:** no call to the Anthropic API, Stellar Horizon, PyPI or npm (so no `pip-audit` / `npm audit`). No `vite build`, because it would overwrite `frontend/dist`. No browser run of the UI. No writes to the project database.

**Status legend:** **PASS**: works as intended, with executed evidence. **FAIL**: defect or missing capability, with executed or code evidence. **NOT VERIFIED**: could not be checked inside the constraints, or no evidence exists. The *Evidence* column says whether a finding was **executed** (test or probe) or is **static** (code reading only).

---

## 2. Actual end-to-end workflow (as implemented)

```
 (no auth)                    (no auth)                         (no auth)
POST /assets ──► POST /assets/{id}/documents ──► POST /documents/{id}/extractions
  asset row        PDF → ext/magic/size check       pages text → AnthropicAIService
                   SHA-256, pypdf page text          (tests: MockAIService only)
                   file → storage/<doc>/v<n>/<fn>    → LegalExtraction(status=draft) + 7 LegalClaims
                         │
                         └─ POST /documents/{id}/versions (no auth) → v2… (old approvals untouched)

 (Bearer)                          (Bearer)                              (Bearer)
POST /extractions/{id}/review ─► PATCH /extractions/{id}/claims/{cid} ─► POST /extractions/{id}/approvals
  draft → under_review              claim mutated in place,                 any reviewer, any status ≠ n/a
  (optional; can be skipped)        ReviewCorrection audit row              → status=approved (one approval suffices)

 (X-API-Key, 60 req/min/client, in-memory)
GET /passport/{network}/{code}/{issuer}
  asset row (live, mutable)  +  latest-created approved extraction across ALL of the asset's documents
  + its claims + approvals (reviewer id hidden) + document title/type/version + disclaimer
```

What runs in practice: everything above works against the **mock** AI and Stellar services. The local database is empty, `ANTHROPIC_API_KEY` is not set in `.env`, and `AnthropicAIService` has no tests. The production AI path has therefore never been run in this codebase.

---

## 3. Implemented vs. mocked vs. placeholder

| Component | Status | Notes |
|---|---|---|
| Asset registry, document storage, versioning, review, passport | **Implemented** | Real code paths exercised by tests |
| `AnthropicAIService` (`app/services/ai.py`) | **Implemented, never executed** | No unit tests for `_parse_and_complete` / `_extract_json_from_text`; no API key configured |
| `MockAIService` (`tests/mock_ai.py`) | **Mock (tests only)** | Returns fixed passages that do not appear in the blank test PDF |
| `StellarService` (`app/services/stellar.py`) | **Implemented, never executed** | Only `MockStellarService` is exercised |
| Reviewer identities (`app/auth.py`) | **Placeholder** | Two hard-coded people ("Alice Chen", "Bob Martinez") mapped from two static `.env` tokens |
| Institutional clients (`app/api_keys.py`) | **Placeholder** | Two hard-coded organisations; `tier="read"` is an unused placeholder |
| Rate limiter (`app/rate_limit.py`) | **Prototype** | In-process memory; resets on restart; not shared across workers |
| "AI cannot approve" guard | **Convention** | Blocks only `user_id` starting with `ai-`; the real guarantee is that the AI service has no token |
| Stellar key validation | **Partial** | Format only; checksum not verified (documented in code) |
| Frontend MSW mocks (`frontend/src/test/mocks/handlers.ts`) | **Mock, diverges from API** | Document, extraction and blockchain mocks use field names the backend never returns |
| Schema migrations | **Missing** | `Base.metadata.create_all` at startup; no Alembic |
| OCR for scanned PDFs | **Missing** | Image-only pages produce empty text |
| Reject / revoke approval, archive document | **Missing** | `DocumentStatus.archived` exists but nothing can set it |

---

## 4. Feature-by-feature readiness matrix

### A. Token / asset registry

| # | Feature | Status | Evidence |
|---|---|---|---|
| A1 | Register asset with Stellar format validation | **PASS** | Executed: 17 tests in `tests/test_assets.py`. Format only; no checksum |
| A2 | Duplicate-asset protection | **FAIL** | Executed P10: same code + issuer on `stellar-testnet` after `stellar-mainnet` → **409**. `uq_stellar_asset` omits `blockchain_network` (`app/models/asset.py:14`) |
| A3 | Blockchain identity immutable after creation | **PASS** | Static + executed: `AssetUpdate` has no blockchain fields (`app/schemas/asset.py:63`); `test_create_response_separates_blockchain_and_legal_fields` |
| A4 | Update legal / organisational metadata | **PASS** (function) / see I1 | Executed: `test_update_asset_*`. No auth and no audit trail; the change flows straight into the passport (F2) |
| A5 | Read-only Stellar Horizon verification | **PASS** (mock) / **NOT VERIFIED** (live) | Executed: 8 tests with `MockStellarService`. Live Horizon not contacted, per audit constraints. Malformed Horizon JSON (`response.json()`, `raw["asset_type"]`) is unhandled → 500 (static, `app/services/stellar.py:127-139`) |

### B. Legal document handling

| # | Feature | Status | Evidence |
|---|---|---|---|
| B1 | PDF-only upload (extension, magic bytes, size limit) | **PASS** | Executed: `test_upload_wrong_file_extension_returns_415`, `..._fake_pdf_magic_bytes_returns_415`, `..._exceeds_size_limit_returns_413` |
| B2 | SHA-256 recorded per version | **PASS** | Executed: `test_upload_document_stores_sha256_hash` |
| B3 | Per-page text extraction | **PASS** (blank PDF) / **NOT VERIFIED** (real text) | The only fixture is a blank page (`tests/conftest.py:146`); no test uses a PDF that contains text |
| B4 | Document versioning and history | **PASS** | Executed: `test_upload_new_version_increments_number`, `test_list_versions_shows_history`. No unique `(document_id, version_number)` index (DB index list), so concurrent uploads are **NOT VERIFIED** |
| B5 | Safe storage paths | **FAIL** | Executed P08 / P08v2: filename `../../../x.pdf` is written **outside** the storage root (HTTP 201); forward-slash absolute path `C:/…/x.pdf` is written at that absolute location, then 500. Cause: `app/routers/documents.py:129` only replaces spaces; `app/services/storage.py:27` joins the raw name |
| B6 | Stored-file integrity | **FAIL** | Executed P08b: an unauthenticated upload overwrote an **approved** document's stored PDF. Download served the tampered file (SHA-256 ≠ recorded) with 200; the passport still served 200. `download_document` never re-checks the hash |
| B7 | Write-ordering / orphan files | **FAIL** (static) | File is written to disk before the DB commit (`documents.py:130` vs `:195`); a failed request leaves an orphan file (seen in P08v2) |
| B8 | Scanned / image-only PDFs | **FAIL** (not implemented) | No OCR; empty text is passed to the AI as "(boş sayfa)" |

### C. AI legal analysis

| # | Feature | Status | Evidence |
|---|---|---|---|
| C1 | Real AI extraction (Anthropic) | **NOT VERIFIED** | Never executed: no API key in `.env`, no tests of `AnthropicAIService`, DB has 0 extractions. Model ID `claude-haiku-4-5-20251001` not validated |
| C2 | JSON parsing and completion of missing fields | **NOT VERIFIED** | `_parse_and_complete` has no unit tests. Code reads correctly, but duplicates or unknown fields from the model are not de-duplicated |
| C3 | AI output always `draft`, never auto-approved | **PASS** | Executed: `test_extraction_status_is_always_draft`; static `extractions.py:119` |
| C4 | Unverified-AI disclaimer on every extraction | **PASS** | Executed: `test_extraction_disclaimer_is_present` |
| C5 | AI failure handling | **FAIL** | Executed P09: `anthropic.APIConnectionError` (network failure or timeout) → `AttributeError: … no attribute 'status_code'` escapes as **HTTP 500** instead of 503 (`app/services/ai.py:176-180`). P09b: empty `content` → `IndexError` → 500 (`ai.py:182`). P09c: any non-`RuntimeError` → raw 500. `stop_reason == "max_tokens"` is not handled (static) |
| C6 | Cost and abuse controls on AI trigger | **FAIL** | Executed P01, P11: `POST /documents/{id}/extractions` needs no credentials and has no rate limit (5/5 calls accepted with a 1-request limiter installed) |
| C7 | Long documents (context window, chunking, truncation) | **NOT VERIFIED** | Whole document sent in one message, `max_tokens=4096`; no chunking (static) |
| C8 | Prompt-injection resistance | **NOT VERIFIED** | Document text is placed directly in the user message; the only mitigation is human review, and passages are not verified (D2) |

### D. Source references (evidence trail)

| # | Feature | Status | Evidence |
|---|---|---|---|
| D1 | Each known claim stores page + supporting passage | **PASS** | Executed: `test_known_claims_have_source_references`; columns `source_page`, `supporting_passage` |
| D2 | Passage is verified to exist in the source document | **FAIL** | Executed P02: blank PDF (0 text characters) → mock passages approved (201) → passport shows **`verified: true` on 4 fields** whose quotes do not exist in the document. `verified` only means "passage is non-empty" (`app/routers/passport.py:181-184`) |
| D3 | Cited page is within the document | **FAIL** | Executed P02b: correction with `source_page=999` on a 1-page PDF plus a fabricated passage → 200; approval → 201 |
| D4 | Reference pinned to exact document version | **PASS** | Static + executed: `LegalExtraction.version_id`; passport returns `source_document.version_number` |
| D5 | Correction audit captures source-page changes | **FAIL** | Executed P06: `ReviewCorrection` has no original or corrected `source_page`; page edits are not audited (`app/models/review.py:37-45`) |

### E. Human review and approval

| # | Feature | Status | Evidence |
|---|---|---|---|
| E1 | Review endpoints require Bearer auth (403 missing, 401 invalid) | **PASS** | Executed: `test_start_review_unauthenticated`, `test_start_review_invalid_token` |
| E2 | AI identity cannot approve | **PASS** | Executed: `test_approve_ai_forbidden` (naming-convention guard; see §3) |
| E3 | Approval blocked if a known claim lacks a passage | **PASS** | Executed: `test_approve_missing_source_evidence`. Checks non-emptiness only (D2) |
| E4 | Approved extraction cannot be corrected or re-reviewed | **PASS** | Executed: `test_correct_claim_approved_rejected`, `test_start_review_already_approved_conflict` |
| E5 | State machine: approve only after review | **FAIL** | Executed P05: approved directly from `draft` → 201. The UI offers it too (`ExtractionDetail.tsx:391`) |
| E6 | Independent legal verification required for an authoritative passport | **FAIL** | Executed P05: a single `issuer_attestation` is enough for the passport to be served. Needs a product decision |
| E7 | One approval per reviewer | **FAIL** | Executed P05: the same reviewer approved twice → 2 approvals shown in the passport chain |
| E8 | Reject / revoke / withdraw approval | **FAIL** (not implemented) | No endpoint or status. A wrong approval stays in the passport until a newer approval replaces it |
| E9 | Correction audit trail | **PASS** (partial) | Executed: `test_correct_claim_success_audit_record`. Gaps: D5, and P06: `is_unknown=true` with a substantive value is accepted and served |
| E10 | Who started the review is recorded | **FAIL** (static) | `start_review` returns the reviewer but persists nothing (`review.py:88-97`) |
| E11 | Approval binds to exact content (hash of claims + document) | **FAIL** (static) | `ReviewApproval` stores no snapshot or hash; with B6, what was approved cannot be proven |

### F. Versioning and stale approvals

| # | Feature | Status | Evidence |
|---|---|---|---|
| F1 | New document version invalidates or flags prior approval | **FAIL** | Executed P03: after v2 upload, the passport still returns 200 with `version_number: 1`; the extraction stays `approved`; the response has no stale or superseded marker |
| F2 | Asset legal metadata changes require re-approval | **FAIL** | Executed P04: an **unauthenticated** `PATCH /assets/{id}` changed the passport's `jurisdiction` from `US-NY` to `KY-CAYMAN` and the issuer organisation name, with no re-approval |
| F3 | Retired or suspended asset reflected in passport | **FAIL** | Executed P04: `status=retired` asset → passport 200; the asset block has no `status` field |
| F4 | Coherent passport for assets with several documents | **FAIL** | Executed P07: two approved documents → the passport shows only the most recently *created* extraction (not most recently approved); `passport_version` counts extractions across unrelated documents |

### G. Institutional API (Legal Passport)

| # | Feature | Status | Evidence |
|---|---|---|---|
| G1 | X-API-Key auth (403 missing, 401 invalid) | **PASS** | Executed: `test_passport_no_api_key_returns_403`, `..._invalid_api_key_returns_401` |
| G2 | Only approved extractions are served | **PASS** | Executed: `test_passport_draft_extraction_returns_404`; static `passport.py:127` |
| G3 | Unapproved findings not exposed by any other API | **FAIL** | Executed P01: `GET /extractions/{id}` and `GET /documents/{id}/extractions` return draft claims with no credentials |
| G4 | Rate limiting | **PASS** (single process) | Executed: `test_passport_rate_limit_exceeded_returns_429`. In-memory and per-process |
| G5 | Request logging without secrets | **PASS** | Executed: `test_passport_request_is_logged`; static: the key is never logged; issuer key truncated to 8 characters |
| G6 | Reviewer identity hidden from clients | **PASS** | Static: `ApprovalEntry` has no `reviewer_id` |
| G7 | Source PDF not obtainable through the institutional channel | **FAIL** | Executed P01 + static: the passport returns `document_id`; `GET /documents/{id}/download` and `/text` are public. This contradicts the design note in `passport.py:13` |
| G8 | Tenant isolation / client entitlements | **FAIL** (not implemented) | Executed P10b: every API key can read every asset; no entitlement model |
| G9 | OpenAPI documentation | **PASS** | Executed: `/openapi.json` 200, 16 paths, 7 tags, `HTTPBearer` + `APIKeyHeader` schemes |

### H. Frontend (React / Vite / TypeScript)

| # | Feature | Status | Evidence |
|---|---|---|---|
| H1 | Type-check | **PASS** | Executed: `tsc --noEmit` exit 0. `vite build` not run (would overwrite `dist/`) |
| H2 | Unit tests | **PASS** | Executed: 25/25. Covers AssetList, PassportPreview and StatusBadge only |
| H3 | Asset detail page with ≥1 document | **FAIL** | Executed contract probe P12: the API returns no top-level `sha256_hash`, but `AssetDetail.tsx:183` calls `doc.sha256_hash.slice(0, 16)` → `TypeError` during render. With no ErrorBoundary, the whole app unmounts. *Browser not run; conclusion from code + live API shape* |
| H4 | Extraction review page | **NOT VERIFIED** | No tests; MSW extraction mock uses non-existent fields (`ai_model_used`, `extracted_at`, `document_version_id`) |
| H5 | Inline reviewer-token entry | **FAIL** (static) | `ExtractionDetail.tsx:356-373`: the input renders only while `token` is empty, so the first keystroke hides it |
| H6 | Upload new version / download / view document text from UI | **FAIL** (not implemented) | `api.ts` has no functions for `POST /documents/{id}/versions`, `/download` or `/text` |
| H7 | Accurate status display | **FAIL** (static) | `Dashboard.tsx:165` shows **"Approved"** for any asset whose status is `active`, whether or not anything was approved |
| H8 | API error display | **FAIL** (static + executed P12c) | FastAPI 422 `detail` is a list; `api.ts:47-49` passes it to `Error()`, so the message is `[object Object]` |
| H9 | Frontend types match backend | **FAIL** | Executed P12/P12b: `Document.sha256_hash` and `DocumentVersion.file_size_bytes` do not exist in API responses |
| H10 | Credential storage | **FAIL** (for production) | Static: reviewer token and institutional key persisted in `localStorage` (`AuthContext.tsx:31,38`), readable by any XSS. Acceptable only for a local demo |

### I. Security, operations, compliance

| # | Area | Status | Evidence |
|---|---|---|---|
| I1 | Authentication coverage | **FAIL** | Executed (route enumeration + P01): **16 of 22 routes are unauthenticated**, including create/patch asset, upload document and version, trigger AI, read drafts, download PDFs |
| I2 | Authorization / roles | **FAIL** | Executed P10b: any issuer reviewer can attest any issuer's asset; no operator or admin role exists for registry and document actions |
| I3 | Tenant isolation | **FAIL** (not implemented) | No organisation or tenant column on any table |
| I4 | CORS | **FAIL** (static) | `allow_origins=["*"]` (`app/main.py:79-84`). With I1, any web page the operator visits can call `http://127.0.0.1:8000` and create or patch data |
| I5 | Secrets handling | **PASS** | Executed scan: `.env` untracked and absent from git history; no secret value in source, tests, README or `frontend/dist`; empty token or key disables it. Limits: static plaintext tokens, no expiry or rotation, no hashing, non-constant-time comparison |
| I6 | Upload safety | **FAIL** | B5, B6, B7. Whole multipart body is buffered before the size check (**NOT VERIFIED** as DoS). pypdf parsing of hostile PDFs (**NOT VERIFIED**) |
| I7 | Error handling consistency | **FAIL** (partial) | C5 (500s), A5 (Horizon parse). Error messages mix Turkish and English |
| I8 | Dependency vulnerability status | **NOT VERIFIED** | `pip-audit` / `npm audit` not run (no external calls). `anthropic>=0.20.0` is unpinned (1.14.0 installed); `pypdf==4.3.1` is several majors behind and should be audited |
| I9 | Database migrations and backups | **FAIL** (not implemented) | `create_all` only; SQLite file; no Alembic, no backup or restore procedure |
| I10 | Deployment / CI | **FAIL** (not implemented) | No Dockerfile, CI workflow or production server config; launcher binds to 127.0.0.1 only (good for local use) |
| I11 | Regulatory / security compliance (SOC 2, ISO 27001, GDPR, MiCA, etc.) | **NOT VERIFIED** | No evidence exists in the repository. **No compliance claim can be made.** |

**Totals (71 rows):** 26 PASS · 38 FAIL · 7 NOT VERIFIED. A mixed row such as "PASS (mock) / NOT VERIFIED (live)" is counted once, under its first status. Three of the PASS rows (A5, B3, G4) hold only for mocks, blank PDFs or a single process.

---

## 5. Prioritized issues

### P0: blockers before any non-local or institutional exposure

| ID | Issue | Matrix refs | Why it matters |
|---|---|---|---|
| **P0-1** | 16 routes have no authentication or authorization, combined with wildcard CORS | I1, I2, I4, C6, G3, G7 | Anyone (or any web page in the operator's browser) can register assets, change jurisdiction or issuer name shown in passports, upload or replace documents, trigger paid AI calls, and read drafts and source PDFs |
| **P0-2** | Path traversal / arbitrary `.pdf` write through upload filename; approved evidence can be overwritten | B5, B6, B7 | Breaks the integrity of the evidence trail; writes outside the storage root |
| **P0-3** | Stale approvals: a new document version and asset metadata changes leave the passport serving outdated or unapproved data as approved | F1, F2, F3 | Institutional clients receive authoritative-looking data that no reviewer approved |
| **P0-4** | `verified: true` does not mean the quote exists in the document; page numbers unchecked | D2, D3 | The central trust claim of the product is not enforced |

### P1: required for a credible pilot

| ID | Issue | Matrix refs |
|---|---|---|
| **P1-1** | Approval policy: enforce `under_review` before approval, one approval per reviewer, configurable quorum (e.g. issuer attestation **and** independent legal verification), reject and revoke | E5–E8 |
| **P1-2** | Tenant and entitlement model: bind issuer reviewers to their issuer organisation; scope institutional keys | G8, I2, I3 |
| **P1-3** | Real AI path never exercised; error mapping bugs (500 on connection error or empty content); no truncation handling; no cost controls | C1, C2, C5–C7 |
| **P1-4** | Frontend breaks on real data: AssetDetail crash, mismatched types, mocks that don't match the API, untested workflow pages | H3, H4, H9 |
| **P1-5** | Audit completeness: source-page changes, review starter, content hash and snapshot on approval, `is_unknown` vs value consistency | D5, E9–E11 |

### P2: hardening and operability

| ID | Issue | Matrix refs |
|---|---|---|
| **P2-1** | Multi-document passport semantics (which document governs which field; versioning across documents) | F4 |
| **P2-2** | Data constraints: uniqueness including network; unique `(document_id, version_number)`; write file after commit, or clean up on failure | A2, B4, B7 |
| **P2-3** | Frontend UX defects: token input, 422 rendering, misleading "Approved" badge, missing version upload, download and text views, credentials in `localStorage` | H5–H8, H10 |
| **P2-4** | Operations: Alembic migrations, PostgreSQL, shared rate limiter, structured audit log, CI, dependency audit, pinned `anthropic` | I8–I10, G4 |
| **P2-5** | Stellar service robustness (malformed Horizon JSON) and live testnet check (opt-in) | A5 |

---

## 6. Probe evidence (executed during this audit)

The probe scripts live outside the repository, in this session's scratch folder (`audit_probes.py`, `probe_p08.py`, `probe_p08b.py`). Each probe started a fresh in-memory database and a temporary storage root, used the project's own mocks and fictional test credentials, and made **no network calls**.

| Probe | Observed result |
|---|---|
| P01 | With **no credentials**: `POST /assets` 201, `PATCH /assets/{id}` 200, `POST documents` 201, `POST versions` 201, `POST extractions` 201, `GET` draft extraction 200, `GET download` 200, `GET text` 200 |
| P02 | Document text = 0 characters; approval 201; passport `verified=true` on 4 fields whose passages are absent from the document |
| P02b | Correction with `source_page=999` on a 1-page PDF and a fabricated passage → 200; approval → 201 |
| P03 | After v2 upload: `current_version_number=2`; passport 200 with `source_document.version_number=1`; no stale marker |
| P04 | Unauthenticated PATCH changed passport `US-NY / Meridian Property Holdings LLC` → `KY-CAYMAN / Unrelated Corp`; `retired` asset still served (200) |
| P05 | Approved from `draft` by issuer reviewer → 201; same reviewer again → 201; passport approvals = `[issuer_attestation, issuer_attestation]` |
| P06 | Correction response has no page fields; `is_unknown=true` with value "Some replacement value" served in passport |
| P07 | Two approved documents → passport shows only "Legal Opinion"; `passport_version=2` |
| P08 / P08v2 | `../../../rel_escape.pdf` → 201, file written outside the storage root; `C:/…/fwd_abs_escape.pdf` → file written at that absolute path, then 500 |
| P08b | Upload with filename `../../<docA>/v1/evidence.pdf` → 201; approved doc A now serves a file whose SHA-256 ≠ the recorded hash; passport still 200 |
| P09 | `APIConnectionError` → `AttributeError` escapes → would be HTTP 500 |
| P09b | Response with no content blocks → `IndexError` → HTTP 500 |
| P09c | Non-`RuntimeError` from the AI layer → HTTP 500 "Internal Server Error" |
| P10 | Same code + issuer on testnet after mainnet → 409 |
| P10b | Issuer reviewer attested another organisation's asset → 201; both institutional keys read it (200/200) |
| P11 | 5 unauthenticated AI triggers with a 1-request limiter → `[201, 201, 201, 201, 201]` |
| P12 / P12b | Document list items lack top-level `sha256_hash`; versions return `file_size`, not `file_size_bytes` |
| P12c | 422 `detail` is a list |

Harness note: one artefact was excluded from the findings. A document row with no version appeared only because the probe harness, like `tests/conftest.py`, shares one DB session across requests; production uses a session per request, which rolls back on error. The orphan **file** from that failure is real (B7).

---

## 7. What is already solid

- Clear separation of AI drafts from approved data, with mandatory disclaimers on extraction, approval, blockchain and passport responses.
- Approval derives `approval_type` from the reviewer role; the client cannot choose it.
- Approved extractions are frozen against correction and re-review.
- The passport hides reviewer identities and never returns drafts.
- Secrets hygiene: env-only credentials, nothing in git history or the bundle, empty values disable access, keys never logged.
- Stellar integration is strictly read-only, with no key material anywhere.
- Test isolation is good (fresh in-memory DB, temp storage, dependency overrides), and both suites run fast and green.

---

## 8. Proposed next steps — Stage 9B (awaiting approval)

Each step should land with regression tests that turn the §6 probes into permanent tests. The order follows priority; steps 1–4 close the P0 items.

1. **Access control baseline (P0-1).** Add a minimal role model (`operator`, `issuer_reviewer`, `legal_reviewer`), require auth on all 16 open routes, restrict CORS to the configured frontend origin, and add rate limiting or an explicit operator-only gate on AI extraction. *Acceptance:* every route except `/health` returns 401/403 without credentials, and probes P01 and P11 fail.
2. **Safe storage (P0-2).** Store files under a server-generated name (e.g. `original.pdf`), keep `original_filename` as metadata only, resolve the path and assert it stays inside the storage root, write after the DB flush with cleanup on failure, and verify SHA-256 on download (refuse on mismatch). *Acceptance:* P08, P08v2 and P08b fail.
3. **Staleness and invalidation (P0-3).** Add `superseded` (and `revoked`) extraction states. A new document version supersedes approvals on older versions. Snapshot the asset's legal metadata at approval time and serve the snapshot, or require re-approval when it changes. Expose asset status and a `stale` / `superseded` indicator in the passport, and decide whether retired or suspended assets return 410/409 or a flagged passport. *Acceptance:* P03 and P04 fail.
4. **Evidence verification (P0-4).** On approval and on correction, check that each `supporting_passage` appears (whitespace and case normalised) on `source_page` of the exact version, and that the page is within range. Store the verification result per claim, and derive passport `verified` from it rather than from non-emptiness. Image-only pages can never be `verified`. *Acceptance:* P02 and P02b fail.
5. **Approval policy (P1-1).** Require `under_review` before approval, allow one approval per reviewer, make the quorum configurable (recommended default: issuer attestation + independent legal verification before the passport is served), and add reject and revoke endpoints with audit rows.
6. **AI hardening (P1-3), with no paid calls in CI.** Fix the `APIConnectionError` / empty-content / `max_tokens` handling, add unit tests for `_parse_and_complete` with recorded fixtures, and add an **opt-in, manually triggered** evaluation script for a real API key on sample documents (only with your explicit approval).
7. **Frontend contract repair (P1-4).** Fix the AssetDetail crash and type mismatches. Generate types from `/openapi.json`, or at least align them. Rewrite the MSW mocks to the real schemas, add an ErrorBoundary, and add tests for AssetDetail and ExtractionDetail.
8. **Audit completeness (P1-5).** Record page changes in corrections and the review starter. Store a content hash (claims + document SHA-256 + asset snapshot) on each approval.
9. **Decisions needed from you before 9B starts:**
   - (a) Should one issuer attestation be enough for a passport, or is independent legal verification mandatory?
   - (b) How should multiple documents per asset combine into one passport?
   - (c) Should institutional clients be scoped to specific assets or issuers?
   - (d) Should retired or suspended assets return an error or a flagged passport?

Tenancy, PostgreSQL / Alembic, CI and dependency auditing (P1-2, P2-x) fit best in a later Stage 9C, unless you want them pulled forward.

---

## 9. Stage 9B-1 status — access control (2026-10-15)

Scope: authentication, authorization, tenant isolation, CORS. Implemented on top of `07ad72f`, uncommitted. Reference documentation: [ACCESS_CONTROL.md](ACCESS_CONTROL.md). The §4 matrix above is the original 9A record and has not been edited; this table records only what changed.

| Row | 9A | Now | Evidence (executed) |
|---|---|---|---|
| I1 Authentication coverage | FAIL | **PASS** | 22 of 23 routes require credentials; only `GET /health` is public. `test_route_inventory_matches_expected_guards`, `test_protected_route_without_credentials_is_rejected` (all 22), `test_bearer_route_with_invalid_token_returns_401` (all 21) |
| I2 Authorization / roles | FAIL | **PASS** | Role → permission matrix (`app/permissions.py`). `test_issuer_reviewer_cannot_register_or_modify_assets`, `test_legal_reviewer_cannot_upload_or_run_ai`, `test_platform_admin_cannot_review_correct_or_approve` |
| I3 Tenant isolation | FAIL | **PASS** (issuer level) | Immutable `assets.issuer_id`, inherited by all child resources. `test_cross_tenant_resource_access_returns_404` (17 operations), `test_cross_tenant_attempts_have_no_side_effects`, `test_asset_list_is_scoped_to_issuer` |
| I4 CORS | FAIL | **PASS** | Explicit validated allowlist, wildcard rejected. `test_cors_*` (4 tests + 6 rejected values) |
| G3 Drafts not exposed elsewhere | FAIL | **PASS** | Draft endpoints require `extraction:read` and tenant scope; API keys get 403. `test_institutional_key_cannot_read_drafts_or_download` |
| G7 Source PDF via institutional channel | FAIL | **PASS** | Download and text require a user token with `document:read` in scope. Same test as above |
| G8 Institutional entitlements | FAIL | **PASS** | Keys scoped to `issuer_ids` / `asset_ids` + `operations`. `test_institutional_key_cannot_read_other_issuer_passport`, `test_asset_scoped_institutional_key`, `test_institutional_key_without_passport_operation_is_forbidden` |
| C6 Cost / abuse controls on AI trigger | FAIL | **PASS** | `extraction:run` + per-user hourly limit enforced before the AI call. `test_extraction_trigger_is_rate_limited_per_user` |
| I5 Secrets handling | PASS | **PASS** (stronger) | Credentials stored only as SHA-256 hashes in a gitignored file; no plaintext secret in code. `test_valid_config_builds_hash_keyed_registry`, `test_tool_*` |
| H10 Credential storage in browser | FAIL | **PARTIAL** | Moved to tab-scoped `sessionStorage`; legacy `localStorage` copy purged (`AuthContext.test.tsx`). Still readable by XSS |

Probes P01 and P11 are now permanent regression tests (I1 and C6 rows). A mutation check disabled each control in memory: tenant check, list filter, institutional scope, operation check, and role matrix. Each mutation made at least one test fail.

Still open from P0: P0-2 (upload path traversal, 9B-2), P0-3 (stale approvals, 9B-3), P0-4 (passage verification, 9B-4). Decision 1 (issuer attestation alone must not give independently verified status) is not enforced yet; the passport is still served after an issuer-only approval (9B-5).

---

## 10. Stage 9B-2 status — secure PDF upload, storage, download (2026-10-15)

Uncommitted, on top of 9B-1. The §4 matrix remains the 9A record.

| Row | 9A | Now | Evidence (executed) |
|---|---|---|---|
| B5 Safe storage paths | FAIL | **PASS** | Storage path is `<document_id>/v<n>/<random-uuid>.pdf`; the client filename is sanitized display metadata only. Every path is resolved and confined to the storage root. Tests: `test_p08_relative_traversal_*` (4 payloads), `test_p08v2_absolute_and_windows_paths_*` (posix, drive, backslash, `\\?\`, UNC, drive-relative), `test_storage_rejects_unsafe_stored_paths` (11 cases), `test_storage_blocks_link_escape_on_write/read` |
| B6 Stored-file integrity | FAIL | **PASS** | SHA-256 is verified against the bytes re-read from disk after writing. Downloads re-hash and refuse on mismatch (500, logged). Files are created exclusive-only (`O_EXCL`). Tests: `test_p08b_upload_cannot_overwrite_another_documents_file`, `test_tampered_file_is_not_served`, `test_corrupted_stored_path_in_db_cannot_read_outside`, `test_storage_paths_are_unique_and_never_overwrite` |
| B7 Write ordering / orphan files | FAIL | **PASS** | A failed commit or failed write deletes the file written by that request. Rejected uploads leave nothing on disk. Tests: `test_invalid_pdfs_are_rejected_without_side_effects`, `test_rejected_uploads_never_touch_storage` |
| B3 Text extraction (real text) | NOT VERIFIED | **PASS** | Generated text-bearing PDF fixtures (`tests/pdf_fixtures.py`). `test_text_pdf_upload_extracts_text_and_round_trips` |
| B4 Concurrent version uploads | NOT VERIFIED | **PASS** | Atomic `UPDATE … RETURNING` version allocation plus unique indexes on `(document_id, version_number)` and `stored_path`. `test_concurrent_version_uploads_get_distinct_versions`: 8 parallel uploads on file-based SQLite with per-request sessions; all returned 201, versions were distinct and contiguous, no orphan files |
| I6 Upload safety | FAIL | **PASS** (with limits below) | Empty, magic-less, truncated (no `%%EOF`), unparseable, encrypted and zero-page PDFs are rejected. Configurable page, text and parse-time limits. A request-size cap is enforced before buffering, both via `Content-Length` and while streaming |

Probes P08, P08v2 and P08b are now permanent regression tests. A mutation check disabled each control in memory: root confinement, download hash check, exclusive create, body-size middleware. Each mutation made at least one test fail. The live run over real HTTP (29/29) aimed the original exploit payloads at a canary folder; no file was created or changed outside storage.

Dev DB: backed up to `backups/rwa_legal_passport.pre-9b2.20261015.db`. The two unique indexes were added in place (DB was empty), and the schema now matches a fresh `create_all`. The startup check now also refuses databases that lack named indexes.

**Remaining risks**
- **Per-page CPU/memory in pypdf 4.3.1.** The time budget is checked between pages and as an outer timeout, but a single malicious page can't be interrupted, and the worker thread keeps running after the request times out. Fix: upgrade pypdf to a version with built-in decompression limits, or parse in a killable subprocess (9C dependency work).
- **Symlink escape is tested via a directory junction on this machine,** because Windows doesn't allow unprivileged symlinks here. The symlink branch runs automatically where permitted.
- **Grossly oversized bodies get no clean 413.** Above 4× the limit (max 64 MiB), the body isn't drained, so clients may see a connection reset instead of the 413.
- **Content checks don't inspect PDF features.** JavaScript and embedded files are not scanned. Downloads go out as `attachment` with `nosniff` and `no-store`, and the server never renders PDFs.
- **No malware scanning,** and no storage encryption at rest.
- **SQLite write lock is held during the file write;** heavy concurrency surfaces as retryable 409/503.

---

## 11. Stage 9B-3 status — legal approval version integrity (2026-10-15)

Uncommitted, on top of 9B-2 and the English migration. Reference documentation: [APPROVAL_INTEGRITY.md](APPROVAL_INTEGRITY.md). The database migration was **applied to the local development database** on 2026-10-15, after approval ([MIGRATION_9B3_PLAN.md](MIGRATION_9B3_PLAN.md)).

| Row | 9A | Now | Evidence (executed) |
|---|---|---|---|
| F1 New version invalidates prior approval | FAIL | **PASS** | Approvals bind to the exact version and SHA-256; a newer version makes them `superseded`; the passport returns 409; nothing is transferred. `test_v2_upload_supersedes_v1_approval_and_nothing_is_transferred`, `test_approval_of_superseded_document_version_is_rejected` |
| F2 Asset metadata changes require re-approval | FAIL | **PASS** | `assets.material_revision` increments on every material change; approvals become `stale`; re-approval requires an explicit re-review; reverting values does not restore them. `test_material_asset_change_makes_approval_stale_until_re_review`, `test_non_material_patch_keeps_approval_current` |
| F3 Retired / suspended asset reflected | FAIL | **PASS** | Passport 409 for non-active assets; no approvals accepted while not active; `asset.status` in the passport. `test_suspended_asset_has_no_current_passport_and_accepts_no_approvals` |
| F4 Several documents | FAIL | **PASS** | Each document contributes its current approved extraction; disagreeing values are flagged `conflict` with all sources; non-current documents are listed in `excluded_documents`. `test_conflicting_evidence_across_documents_is_never_silently_accepted`, `test_document_with_superseded_approval_is_excluded_from_passport` |
| E11 Approval binds to exact content | FAIL | **PASS** | Evidence and asset snapshots plus SHA-256 digests and a binding digest on every approval; tampering is detected. `test_approval_is_bound_to_exact_version_evidence_and_asset`, `test_tampered_binding_or_evidence_is_never_current` |
| E6 Independent legal verification | FAIL | **PARTIAL** | Issuer attestation is never reported as independent verification (`verification_level`, `independently_verified`). `test_issuer_attestation_is_never_reported_as_independent_verification`. Whether an issuer-only passport is served at all is the 9B-5 quorum decision |

**Concurrency.** Approval, re-review and correction take the SQLite write lock before checking. Currency is evaluated on every read. Tested with real parallel requests and with forced interleavings (file SQLite, a session per request):
- upload committing inside the check/write window;
- approval holding the lock while an upload, asset change or correction waits.

**History.** Approval records are never modified. `GET /extractions/{id}/approvals` (audit:read, issuer scope) returns every approval with its snapshot and evaluated currency.

**Mutation check.** 16 mutations each disabled one control on a scratch copy, and every one made at least one test fail. The controls covered:
- the superseded-version check, the re-review gate and the active-asset gate;
- the write lock;
- each currency rule (version, asset, evidence, binding integrity, legacy);
- the revision increment;
- the passport's current-only filter, active-asset check, conflict detection and verification level;
- the migration rollback.

**Tests:**
- backend 307 (283 + 19 integrity + 5 migration);
- frontend 40 (34 + 6);
- `tsc` exit 0; production build OK;
- live HTTP on migrated scratch copies: 9B-3 31/31, local setup 37/37, error messages 28/28, secure storage 29/29.

**Remaining risks**
- **Other databases.** The migration was applied to the local development database only. Any other database created before Stage 9B-3 must be migrated the same way; otherwise the application refuses to start on it.
- **Passage verification (P0-4) is still open.** `verified` means only that a passage exists (9B-4).
- **Approval policy (P1-1) is still open (9B-5):** no `under_review` requirement before the first approval, no one-approval-per-reviewer rule, no quorum, no reject or revoke. An issuer-only passport is still served, but it is labelled `issuer_attested`.
- **Dashboard "Approved" label (H7).** The Dashboard still shows "Approved" for every active asset. The extraction and passport pages now show approval currency.
- **SQLite only.** Serialization relies on SQLite's single-writer lock. A multi-writer database needs row locks (`SELECT … FOR UPDATE`) — 9C.

## 12. Stage 9B-4 status — legal evidence verification (2026-10-16)

Uncommitted, on top of 9B-3. Reference documentation: [EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md). The step 7 migration was **applied to the local development database** on 2026-10-16, after approval ([MIGRATION_9B4_CORRECTION_EVIDENCE.md](MIGRATION_9B4_CORRECTION_EVIDENCE.md)). Sections 1–11 above are kept as written. Statements they contain that no longer hold are listed in §12.6.

### 12.1 What was implemented

| Step | Result | Deviation from the plan |
|---|---|---|
| 1 | Pure quotation check `app/evidence.py`: exact normalized substring on token boundaries, ≥ 20 characters and 3 words, per page, no OCR | Case is preserved and NFC is used instead of NFKC with case folding (decision Q2) |
| 2 | `claims[].evidence` on every extraction read, against the extraction's own version | — |
| 3 | AI output gate: duplicate fields rejected, canonical unknowns, strict JSON, truncation and connection errors → 503 | — |
| 4 | Approval gate: every known claim must be verified; 422 with `evidence_failures` | — |
| 5 | Evidence snapshot format 2, `source_text_changed`, `evidence_not_verified`; stored PDF re-checked at approval (409 `source_integrity_failure`) | — |
| 6 | Passport semantics: `verified` from the check, `independently_verified` needs it, `value_origin`, `verification_summary`, PDF re-hash on every read | `no_verified_claims` not added (open decision, issue B) |
| 7 | Correction evidence rules; page history in three new nullable columns; migration applied | Three columns instead of two (`evidence_audit_version`), approved |
| 8 | `GET /documents/{id}/versions/{n}/text` with optional `version_id` | A path parameter instead of `?version_number=` on `/text`; `/text` unchanged plus `version_id` |
| 9 | Frontend: evidence badges, source text panel, correction form, AssetDetail crash and Dashboard label fixed, mocks on the real schema | — |
| 10 | English AI disclaimer, English prompt markers and quotation rule, language policy without application exceptions | Disclaimer wording differs from the D1 proposal (stays accurate after approval; avoids "verified") |
| 11 | This section, `EVIDENCE_VERIFICATION.md`, the step 7 migration document; corrections to `APPROVAL_INTEGRITY.md`, `ACCESS_CONTROL.md`, `README.md` | Scope widened by the step 11 instruction (security review, acceptance assessment); documentation only |

### 12.2 9A rows affected

| Row | 9A | Now | Evidence (executed) |
|---|---|---|---|
| D2 Passage is verified to exist | FAIL | **PASS** | `test_evidence_verifier.py`, `test_extraction_evidence.py`, `test_approval_evidence_gate.py` (blank-PDF probe P02 now refused with 422) |
| D3 Cited page within the document | FAIL | **PASS** | `page_out_of_range` refuses corrections and approvals (P02b): `test_correction_evidence.py`, `test_approval_evidence_gate.py` |
| D5 Correction audit captures page changes | FAIL | **PASS** | `original_source_page`, `corrected_source_page`, `page_history_recorded`: `test_correction_evidence.py`, `test_migration_9b4.py` |
| E3 Approval needs evidence | PASS (non-empty only) | **PASS** | Now a verified quotation on the cited page of the approved version |
| E9 Correction audit trail | PASS (partial) | **PASS** | An unknown claim can no longer keep a value or evidence; page history recorded |
| C2 JSON parsing and completion | NOT VERIFIED | **PASS** | `test_ai_output_integrity.py` (recorded fixtures, no paid call) |
| C5 AI failure handling | FAIL | **PASS** | Connection error, empty content and `max_tokens` → 503, nothing stored |
| C1 Real AI extraction | NOT VERIFIED | **NOT VERIFIED** | Unchanged: no real model call was made (9B-6, needs approval) |
| C8 Prompt-injection resistance | NOT VERIFIED | **NOT VERIFIED** | Mitigated only: every known value needs a verified quotation and human approval; the value itself is still the model's interpretation |
| E6 Independent legal verification | FAIL → PARTIAL | **PARTIAL** | Labels accurate (issue B); quorum is 9B-5 |
| H3 Asset detail with documents | FAIL | **PASS** | `frontend/src/test/AssetDetail.test.tsx` |
| H4 Extraction review page | NOT VERIFIED | **PASS** | `ReviewSourceText.test.tsx`, `CorrectionForm.test.tsx` (jsdom, not a real browser) |
| H6 Version / download / text views | FAIL | **PARTIAL** | Version-specific text view implemented; version upload and download are still not in the UI |
| H7 Accurate status display | FAIL | **PASS** | Dashboard shows the registry status; approval only from `approval_state` |
| H8 API error display | FAIL | **PASS** | Validation lists rendered; rule-specific correction and approval messages |
| H9 Frontend types match backend | FAIL | **PASS** | Types and MSW mocks follow the backend schemas; `tests/test_ai_disclaimer.py` checks mock wording against the backend |

P0-4 is closed.

### 12.3 Verification (executed during step 11)

| Check | Result |
|---|---|
| Backend `pytest` | **822 passed** (`test_language_policy` 4, `test_correction_evidence` 69, `test_approval_evidence_gate` 59, `test_approval_integrity` 19, `test_evidence_binding` 75, `test_version_text` 34, `test_access_control` 105, `test_passport_verification` 34, …) |
| Frontend Vitest | **125 passed** |
| TypeScript `tsc --noEmit` | 0 errors |
| Production build | PASS on an isolated scratch copy (`tsc` + `vite build`); `frontend/dist` untouched (hash-checked) |
| Mutation checks, Steps 1–10 (scratch copies) | See the table below |
| Live HTTP on scratch databases (real server, mock AI, fictional credentials) | See the table below |
| Development database | Read-only checks: both migrations report nothing to change, `integrity_check` ok, 0 rows; SHA-256 unchanged before and after all tests |

**Mutation checks, re-run in step 11.** Each mutation disables one control on a scratch copy; at least one test must then fail.

| Step | Detected | Not detected |
|---|---|---|
| 1 Quotation check | 18/18 | — |
| 2 Evidence on claims | 9/9 | — |
| 3 AI output gate | 14/14 | — |
| 4 Approval gate | 20/21 | G11 (skip the gate for a second approval of an approved extraction): equivalent. It is unreachable because live re-verification (step 5) makes such an extraction non-current first; shown with an instrumented probe in step 7 |
| 5 Evidence binding | 28/28 | — |
| 6 Passport semantics | 16/16 | — |
| 7 Correction rules and migration | 24/24 | — |
| 8 Version-specific text | 21/21 | — |
| 9 Frontend evidence and corrections | 32/33 | U03b (remove only the selection-key guard): equivalent under test. The request-sequence guard still discards stale responses; removing both guards is detected |
| 10 Disclaimer and language | 25/25 | — |
| **Total** | **207/209** | 2 equivalent mutants |

**Live HTTP checks**, each on a real uvicorn server with a scratch database, the fictional test registry and the mock AI:

| Check | Result |
|---|---|
| Step 4 approval gate | PASS. The script was written before step 7; its fabricated-correction case now expects the stronger behaviour (correction refused with 422 `evidence_not_verified`) |
| Step 5 evidence binding | PASS |
| Step 6 passport semantics | PASS |
| Step 7 corrections and migration (unmigrated database refused, migration applied, rules over HTTP) | PASS |
| Step 8 version-specific text | PASS |
| Step 9 frontend components against the live backend (jsdom; includes the step 10 disclaimer checks) | 5/5 PASS |

### 12.4 Security and integrity review

| # | Concern | Control | Evidence | Residual risk |
|---|---|---|---|---|
| 1 | Cross-tenant document access | Issuer scope on every route; out-of-scope = identical 404; version-text 404 for every version number of a foreign document | `test_access_control.py` (cross-tenant attempts on every route), `test_version_text.py` | Scope comes from the access file; no external IdP |
| 2 | Unauthorized review and correction | Permission matrix; approval type from the role; `ai-` users cannot approve | `test_access_control.py` route inventory and role tests | Static, long-lived tokens |
| 3 | Cross-document / cross-version leakage | Evidence uses only the extraction's own `version_id` (fail closed for another document's version); `version_id` mismatch → 409 without text; the frontend discards mismatched responses | `test_extraction_evidence.py`, `test_version_text.py`, `ReviewSourceText.test.tsx` | — |
| 4 | Stale document references | Extractions of superseded versions cannot be approved; old versions are served as themselves, marked not current | `test_approval_integrity.py`, `test_version_text.py` | — |
| 5 | Stale approvals | Currency evaluated on every read, with 12 reasons; passport serves current approvals only | `test_evidence_binding.py`, `test_passport_verification.py` | — |
| 6 | Reuse of outdated quotations | A changed value needs a new page and quotation; unknown clears evidence; the frontend never pre-fills | `test_correction_evidence.py`, `CorrectionForm.test.tsx` | — |
| 7 | Concurrent corrections | SQLite write lock; rules evaluated on the live claim | Concurrency tests in `test_approval_integrity.py`, `test_version_text.py` | **Issue A** (§12.5) |
| 8 | Document and page-text integrity | Hash on download and passport read; file vs page records at approval; format-2 page and quotation hashes | `test_evidence_binding.py`, `test_document_storage.py` | Unkeyed SHA-256: a consistent forgery by someone with database and storage write access stays undetected; the text endpoints do not re-read the file |
| 9 | Internal filesystem paths | Server-generated storage paths; no path in any response | `test_document_storage.py`, `test_version_text.py::test_no_internal_storage_path_is_exposed` | — |
| 10 | Misleading legal verification claims | Four concepts kept apart in API text, disclaimers and UI labels | `test_ai_disclaimer.py`, `EnglishDisclaimers.test.tsx`, `PassportEvidence.test.tsx` | **Issue B** (§12.5) |
| 11 | SQLite concurrency | Single-writer lock serializes every state change | File-SQLite concurrency tests | Guarantees hold for SQLite only (9C) |
| 12 | Tests vs production guarantees | — | — | AI and Stellar are mocked; single process; jsdom instead of a browser; no TLS, deployment, load or penetration test; in-memory rate limiter; public `/docs` |

### 12.5 Open issues

**Issue A — no compare-and-set on corrections.** *(Assessment as of step 11. Fixed for clients that send the new precondition; see §12.8.)*
- **Exists:** yes. A correction that omits page and quotation inherits the evidence stored **at commit time**. Another reviewer's correction committed in between (same value, new evidence) is inherited, although the first reviewer never saw it.
- **Effect on evidence integrity:**
  - The inherited quotation must still verify, so unverified evidence cannot result.
  - A value change without evidence fails closed (422).
  - The audit record states the evidence before and after exactly. The record is complete; what can be wrong is the attribution of reviewer intent (a reviewer "keeps" evidence they did not review).
  - Two concurrent value changes resolve last-writer-wins, and both stay in the audit history.
- **Mitigation:** the write lock removes torn writes and check/write races inside a request, but not this cross-request case. The frontend re-reads the claim before sending, which reduces the window to milliseconds for the UI only; other API clients have no protection.
- **Demo:** not blocking for a single-reviewer demonstration.
- **Production / institutional use with several reviewers:** should be fixed first.
- **Smallest remediation:**
  - an optional, additive request field such as `expected_claim` (`value`, `is_unknown`, `source_page`, `supporting_passage`), compared under the existing lock;
  - mismatch → 409, nothing changed; the frontend sends it.
  - No schema change. Optional keeps the contract backward-compatible; making it mandatory later would be a contract change. Needs approval.

**Issue B — `verification_level` semantics.**
- **Misunderstanding is possible:**
  - `issuer_attested` is also returned when **no** field is verified (a passport of unknown or conflicting fields only, or one approved only by an independent reviewer).
  - The names `independently_verified` / `partially_independently_verified` describe independent **reviewer attestation** of source-verified quotations, not legal verification.
  - A consumer reading only this field can overstate the passport.
- **Frontend:** accurate. It shows "No source-verified fields" when the count is 0, and the labels "Attested by an independent legal reviewer" / "Partly attested …" / "Issuer attested — not independently verified", with the `meaning` text next to them.
- **API text:** the OpenAPI descriptions, `verification_summary` and the disclaimers state the limits. The enum value names remain stronger than their meaning.
- **Contract impact:**
  - a new enum value (`no_verified_claims`) or renaming values breaks consumers with exhaustive enums;
  - an additive field does not.
- **Recommended resolution:**
  - keep `verification_level` unchanged now and point consumers to `verification_summary` (documented);
  - later, with approval, add an additive field (e.g. `attestation_level` with an explicit `no_verified_fields` value) and mark `verification_level` deprecated in its description, before any value change in a versioned API.
- **Update (Stage 9B-5B step 1):** mitigated with additive fields; see §13.

### 12.6 Statements superseded by Stage 9B-4

- §11 "Passage verification (P0-4) is still open": closed (§12.2).
- §11 "Dashboard 'Approved' label (H7)": fixed.
- `APPROVAL_INTEGRITY.md` §3 rule 6 "`verified` means only that source evidence exists": superseded; marked in that document.
- `ACCESS_CONTROL.md` §3: the route table now includes the version-text route.
- `ENGLISH_MIGRATION_FINAL.md` §8, kept as written:
  - risk 2 (mixed-language AI output) is resolved for new records;
  - risk 4 (allowlist update) was done in step 10;
  - risk 6 (Turkish fallback in `test_blockchain.py`) was removed;
  - risk 7 (README structure) was updated.

### 12.7 Acceptance assessment

| Area | Judgement | Basis |
|---|---|---|
| A. Functional completion | **PASS** | Steps 1–10 implemented with their acceptance criteria; deviations decided and documented (§12.1) |
| B. Test coverage and regression integrity | **PASS** | Full suites green; mutation checks of every step re-run; live HTTP checks. Limits: mocked AI, jsdom, SQLite, single process |
| C. API and schema compatibility | **PASS** | Response changes are additive. Intentional behaviour changes: unverified evidence refuses approvals and corrections (422), the new disclaimer text. One approved, reversible migration |
| D. Evidence and document-version integrity | **CONDITIONAL** | Verification, version binding and currency hold under test. Conditions: issue A for multi-reviewer use; unkeyed hashes |
| E. Security and authorization | **CONDITIONAL** | Tenant isolation and roles enforced and tested. Static tokens, in-memory rate limits, public `/docs`, no penetration test |
| F. Documentation completeness | **PASS** | §12, `EVIDENCE_VERIFICATION.md`, migration document, corrected references |
| G. Competition-demo readiness | **CONDITIONAL** | Conditions: issues A and B accepted as documented limitations; a smoke test in a real browser; if the demo uses the real AI model, its output quality is unverified; the uncommitted work should be committed (after approval) or backed up before the demo |
| H. Production and institutional deployment | **FAIL (not ready)** | Open: issues A and B, approval policy (9B-5), real-model evaluation (9B-6), PostgreSQL / row locks / Alembic / CI (9C), identity provider and token expiry, keyed integrity, deployment hardening, legal review of the disclaimers |

**Decision:** Stage 9B-4 meets its planned scope and acceptance criteria. Final closure is **conditional** on an explicit decision about issues A and B: accept them as documented limitations, or schedule the remediations above. No critical defect blocks acceptance of the stage itself. The system is not production-ready. *(Updated in §12.8 after the issue A fix.)*

### 12.8 Addendum — issue A fix and decisions (2026-10-16)

**Decisions recorded:**
- Issue A: a narrowly scoped fix was authorized.
- Issue B: keep `verification_level` unchanged. The limitation and the recommended future additive field (§12.5) stay as documented; no field or value was added.

**Fix.** `PATCH /extractions/{id}/claims/{claim_id}` accepts an optional `expected_claim` with `value`, `is_unknown`, `source_page` and `supporting_passage` exactly as read:
- all four keys are required, strict types, no other keys;
- it is compared with the stored claim **under the existing write lock**, before the correction rules;
- any difference → 409 `{"detail", "correction_conflict": "claim_changed", "changed_fields"}`, and nothing is changed;
- it applies to every kind of correction;
- scope is checked first (other tenants still get 404).

There is no schema change and no migration. The review interface sends `expected_claim` with every correction; on 409 it shows a conflict notice, reloads the claim and never retries automatically. Files:
- backend: `app/schemas/review.py`, `app/correction_rules.py`, `app/routers/review.py`;
- frontend: `frontend/src/types.ts`, `evidence.ts`, `pages/ExtractionDetail.tsx`, `pages/ApiDocs.tsx`;
- documentation: `EVIDENCE_VERIFICATION.md` §2.4.

**Coverage, stated precisely:**
- **Fully prevented** (atomic compare-and-set under the write lock) for clients that send `expected_claim`, including the review interface.
- **Not prevented** for legacy clients that omit it: their evidence-preserving corrections still inherit what is stored at processing time, as before. A test documents this explicitly.
- Closing it for all clients needs the precondition to be **mandatory** for corrections that omit page or quotation. That breaking change would refuse requests valid today; it was not implemented and awaits a decision.

**Verification:**

| Check | Result |
|---|---|
| Deterministic reproduction before the fix (another reviewer's correction committed between read and lock) | Race reproduced: the legacy request inherited unseen evidence |
| `tests/test_correction_concurrency.py` | 33 tests: reproduction, stale refusal with an unchanged database, matching precondition, every state component, every kind of correction, invalid preconditions, approval binding, approved extraction, cross-tenant, legacy behaviour, OpenAPI, parallel reviewers |
| Frontend | `CorrectionConflict.test.tsx` (6 tests); 10 existing exact-body assertions extended with the new field (equally strict) |
| Backend / frontend suites | **855 passed** / **131 passed**; `tsc` 0 errors |
| Mutation check of the fix | 16/17. Not detected: A13, removing the explicit rollback before the 409. Equivalent at the HTTP level: the uncommitted no-op lock update is rolled back when the request's session closes, and no data can change (database snapshots are compared) |
| Mutation checks of steps 4–10, re-run | 166/168 (steps 4 20/21, 5 28/28, 6 16/16, 7 24/24, 8 21/21, 9 32/33, 10 25/25). Not detected: the known equivalent mutants G11 and U03b. One pattern of the step 7 script had to be made specific, because the new precondition model also declares `is_unknown: StrictBool`. Steps 1–3 are unaffected (their code is unchanged since the step 11 run, 41/41) |
| Live HTTP, real server and scratch database | Issue A check PASS (stale keep → 409 with the database unchanged; reload → 200; legacy request still inherits; other tenant 404; parallel rounds hold the invariant; approval binds the saved state). Step 7 check PASS. Step 9 interface-against-backend check 5/5 (the interface's corrections now carry the precondition) |
| Development database | SHA-256 unchanged before and after all runs |

**Updated acceptance:**
- **D. Evidence and document-version integrity:** **PASS** for the review interface and every client that sends the precondition. Remaining conditions: legacy clients (above) and unkeyed hashes.
- **G. Competition demo:** CONDITIONAL. Issue A no longer conditions the demo, which uses the interface. The other conditions stand: real-browser smoke test, real-model output unverified, commit or backup.
- **H. Production:** FAIL (not ready), unchanged.
- **Stage 9B-4 closure** still needs your explicit approval. Open: the decision whether to make the precondition mandatory (breaking), and issue B as an accepted limitation.

*(§12.8 records the optional precondition as it stood before §12.9.)*

### 12.9 Addendum — mandatory precondition (2026-10-16)

**Decision recorded:** making `expected_claim` mandatory for every reviewer correction was explicitly authorized, as a breaking change for clients that omit it. Issue B is unchanged: `verification_level` is kept, and its limitation is still documented (§12.5).

**Callers inspected before the change:**
- The only internal caller is the review interface (`frontend/src/api.ts` `correctClaim`, used by `ExtractionDetail`), which already sent the precondition. No `app/` or `scripts/` code calls the endpoint.
- The launcher serves the interface from source (`npm run dev`).
- Institutional API keys cannot call it (403).
- No active external client dependency was found in the repository.
- One inactive artifact: the stale production build in `frontend/dist` (2026-10-15, gitignored, not served by the launcher) still calls the endpoint without the precondition. Its corrections would now get 422. Rebuilding it overwrites `dist` and needs approval.

**Contract:**
- **Before:** `expected_claim` optional; omitted or null meant no check.
- **After:** `expected_claim` required (`ExpectedClaimState`; null not allowed). Missing, null, incomplete or malformed → 422 (FastAPI validation list); nothing is read or changed. Matching → the correction proceeds under the existing rules. Different → 409 `claim_changed` as before.
- OpenAPI marks the field required and carries request examples. No other endpoint, field or status changed. No schema change, no migration.

**Interface:** the correction form keeps the claim **as shown when it was opened** and sends that state, so a reload of the page while the form is open never replaces it. The pre-submit re-read only refuses to send; it never supplies the precondition. On 409 the form closes, names what changed, reloads the claim, and never retries automatically.

**Files:**
- backend: `app/schemas/review.py`, `app/routers/review.py`, `app/correction_rules.py` (docstring);
- frontend: `frontend/src/types.ts`, `evidence.ts`, `pages/ExtractionDetail.tsx`, `pages/ApiDocs.tsx`;
- tests: `tests/test_correction_concurrency.py`, plus the existing tests that send corrections (`test_correction_evidence`, `test_approval_evidence_gate`, `test_review`, `test_evidence_binding`, `test_approval_integrity`, `test_access_control`, `test_extraction_evidence`, `test_ai_disclaimer`), which now state the claim as they read it, re-reading where they correct the same claim again; `frontend/src/test/CorrectionConflict.test.tsx`;
- documentation: `EVIDENCE_VERIFICATION.md`.

No assertion was weakened. The language-policy fixture lines are unchanged: `test_review.py` adds the precondition through a helper instead of editing them.

**Verification:**

| Check | Result |
|---|---|
| Backend / frontend suites | **858 passed** / **133 passed**; `tsc` 0 errors |
| Mutation check of the mandatory precondition | 20/21. Not detected: M15, removing the explicit rollback before the 409 (equivalent, as A13 in §12.8). Detected, among others: precondition made optional again, null accepted, any component not compared, check limited to some request kinds, no write lock; in the interface, a form that adopts a reloaded claim, and a precondition fabricated from a fresh read |
| Mutation checks of steps 4–10, re-run | 166/168 (steps 4 20/21, 5 28/28, 6 16/16, 7 24/24, 8 21/21, 9 32/33, 10 25/25). Not detected: the known equivalent mutants G11 and U03b. The step 7 script's pattern for unknown request keys was adapted to the request model's new multi-line configuration |
| Live HTTP, real server and scratch database | Issue A check PASS (stale keep 409 with the database unchanged; legacy request without and with null precondition 422 with the database unchanged; other tenant 404; parallel rounds hold the invariant; approval binds the saved state); steps 4, 6, 7 PASS (scripts updated to send the precondition); step 9 interface against the backend 5/5 PASS (the interface's corrections were accepted by the real backend with the mandatory precondition) |
| Isolated production build | PASS (`tsc` 0 errors, `vite build` OK, no Turkish letters in the bundle); `frontend/dist` unchanged (hash-checked) |
| Development database | SHA-256 unchanged before and after all runs |

**Coverage:** every accepted reviewer correction is now bound, atomically under the write lock, to the claim state its client stated. A request without that state is rejected. No request can inherit evidence saved after its client read the claim. One residual is outside the server's knowledge: a client could fetch the claim just before sending and copy it into the precondition without showing it to the reviewer. The review interface does not, and a frontend test guards it.

**Updated acceptance:**
- **D. Evidence and document-version integrity:** **PASS**. Remaining condition: unkeyed hashes.
- **G. Competition demo:** CONDITIONAL, on a real-browser smoke test, the unverified real-model output, and a commit or backup of the working tree.
- **H. Production and institutional deployment:** FAIL (not ready), unchanged.
- **Stage 9B-4 closure:** recommended once you approve it, with issue B accepted as a documented limitation. The stage is not closed automatically.

## 13. Stage 9B-5B step 1 — truthful verification metadata (2026-10-17)

Scope: issue B only (§12.5), with additive response fields (decision D7). No approval policy, quorum, reviewer deduplication, self-approval rule, withdrawal, policy registry, migration or authentication change.

Five things are kept apart: issuer attestation, legal reviewer approval, source-field verification, actual institutional independence (never verified by this system) and approval-policy satisfaction (not implemented; no field claims it).

| Field | Derived from | Meaning |
|---|---|---|
| `verification_status` | The served `legal_rights` (live quotation check) | `no_source_verified_fields`, `partially_source_verified` (at least one of the seven legal fields unknown, conflicting, not verified or absent) or `all_fields_source_verified`. Independent of approval roles |
| `verification_summary.issuer_attestation_recorded` | Current approvals of the selected extractions | A current `issuer_attestation` exists on at least one served extraction |
| `verification_summary.legal_reviewer_approval_recorded` | Same | A current `legal_verification` exists on at least one served extraction. The role does not prove organizational independence |

- **Unchanged:** `verification_level` (enum, values and calculation), every existing field and value, source selection, error responses and request contract. `independent_reviewer_attestation_recorded` stays, always equal to `legal_reviewer_approval_recorded`.
- **Counted:** current approvals only, of the extraction each document actually serves. Stale, superseded and integrity-failed approvals, and approvals of other extractions, are not counted. No historical record is rewritten or reinterpreted; everything is derived on each read.
- **Not exposed:** reviewer identities, tokens, key hashes, approval ids, internal issuer ids.
- **Residual risk:**
  - Issue B is mitigated, not resolved. A legacy client that reads only `verification_level` can still overstate a passport.
  - `all_fields_source_verified` means quotation presence for every field, not legal correctness.
  - A party with database write access who recomputes the unkeyed digests can still forge approval types (limitation C).
  - The review interface does not display the new fields yet (types only).
- **Tests:** `tests/test_passport_verification_status.py`.

## 14. Stage 9B-5C — approval attribution and B5 consolidation (2026-10-17)

| Fix | Where | Behaviour |
|---|---|---|
| C1 attribution | `evaluate_approval` (`_identity_matches`) | An approval is current only for its own extraction, document, version id and number, compared with the live records; a mismatch or missing context is `binding_integrity_failed` |
| B5 competing extraction | `approve_extraction` | 409 `competing_extraction` when another extraction of the version has a current approval |
| B5 fail-closed passport | `_evaluate_asset` | Several current approved extractions of one document: excluded (`competing_current_extractions`), never resolved by the latest approval |
| Self-review | `approve_extraction`, `_Source.approvals` | 409 `self_review`; historical self-reviewed legal approvals are kept but never counted (`self_reviewed_approval` if it is a document's only approval) |
| Repeated approval | `approve_extraction`, `_Source.approvals` | 409 `duplicate_approval`; historical duplicates count once |
| Request identity | `ApprovalRequest` | `notes` only; unknown fields are 422 |

- **Not changed:**
  - `verification_level` and its calculation;
  - existing response fields;
  - migrations: none;
  - historical records: none rewritten.
- **Additive:** two passport-only `ApprovalStaleReason` values and the 409 `approval_refusal` body.
- **Deferred:**
  - quorum or approval policy;
  - withdrawal and revocation (they need a migration);
  - verified reviewer affiliation;
  - keyed signatures (limitation C).
- **Interim:** an approved extraction is replaced only after its approval stops being current (new document version, asset change).
- **Tests:** `tests/test_approval_attribution.py`, `tests/test_b5_approval_safety.py`.

## 15. Stellar Testnet evidence anchoring (2026-10-17)

Design and limits: [STELLAR_ANCHORING.md](STELLAR_ANCHORING.md).

- **Implemented:**
  - strict StrKey validation of issuer keys;
  - a deterministic evidence manifest (schema v1) and its SHA-256 digest;
  - one Testnet transaction per manifest (memo hash + `manage_data lp:<asset id>`), signed with `stellar-sdk` 16.1.0;
  - idempotent, record-before-submit anchoring with pending, confirmed and failed states;
  - read-only verification against live Horizon data (internal and institutional API);
  - the additive `stellar_anchors` table.
- **Live-verified on Stellar Testnet on 2026-10-17:**
  - transaction `1687c0ddc4a85b07b3ec13e9064aa57a4556e8993b9ff78080b5ac9d628123d2`, ledger 5241718, from account `GADWA6F3V4BTB3M56BWUBRD5AJVX3WSVIGGWN5BQRTHFXKNUKHRWPDOT`;
  - memo and data entry equal the manifest digest `5c760dba…d057`;
  - result `verified`; a new document and a tampered claim both gave `evidence_changed`.
- **Startup change:** the schema check now runs before table creation, so an out-of-date database is refused unchanged. Migration scripts 9B-3 and 9B-4 compare only the tables they govern.
- **Not production:**
  - Testnet resets remove anchors;
  - one anchoring account per deployment;
  - single-process sequence serialization;
  - the anchor attests time and content, never legal validity.
- **Tests:** `tests/test_stellar_anchoring.py`.

## 16. Stage 10A-1 — real PDF ingestion and page-grounded evidence (2026-10-18)

Details: [EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md) §4.4.

- **Already in place (audited, unchanged):**
  - pypdf text extraction per physical page;
  - upload validation (signature, `%%EOF`, encryption, page, size, text and time limits) with nothing stored on rejection;
  - the deterministic quote-to-page check and the evidence gate of approvals;
  - tenant scope on every document route.
- **Added:**
  - the additive `text_layer` summary of every version (derived from the page records; no schema change);
  - the refusal of a real AI call for a version without any text layer (422 `no_extractable_text`);
  - an OCR notice in the document list;
  - real-producer PDF fixtures (headless Chrome, synthetic) and their generator script.
- **Unchanged on purpose:**
  - the extraction mode and the stored page texts. Approvals bind their digests, and approval re-extracts with the upload method;
  - the mock AI path.
- **Real AI extraction:** `AnthropicAIService` exists and is the production dependency, but it has **never been run against a real model**. The quotation accuracy of a real model is unmeasured (limitation G).
- **Tests:**
  - `tests/test_real_pdf_ingestion.py` (backend);
  - `frontend/src/test/TextLayer.test.tsx` (frontend).

## 17. Stage 10A-2 — hardening of the real Anthropic extraction (2026-10-18)

Details: [AI_EXTRACTION.md](AI_EXTRACTION.md).

- **Found:**
  - the production default was the real provider, with no consent mechanism;
  - with no key the request failed with an unhandled `TypeError` (HTTP 500), and with any non-empty key, including the `.env.example` placeholder, the document text was sent to `api.anthropic.com`;
  - the SDK resent requests twice on failure and had a 10-minute timeout;
  - `max_tokens` was fixed at 4096, and a refusal was not recognized;
  - error messages could echo model output;
  - the SDK and the database driver log document text at DEBUG level.
- **Implemented:**
  - `AI_PROVIDER=disabled` by default; the key is a secret and its format is checked; the model must be on the supported list;
  - bounded output tokens, input characters, pages and timeout, with one attempt per request;
  - issuer opt-in (`external_ai_processing`) and a per-request confirmation bound to the version (428 / 409), with the rate limit counted only for requests that are sent;
  - refusal and stop-reason handling, and rejection of ungrounded output;
  - error messages without content; SDK and driver logs without document text; `hide_parameters`;
  - an instruction that document text is untrusted data;
  - a confirmation dialog in the interface;
  - the real-PDF fixtures tracked with pinned SHA-256 values.
- **Unchanged:**
  - mock services, the evidence check and the approval gate;
  - missing fields still become `unknown`;
  - no schema change.
- **Not done:**
  - no live call has been made;
  - consent is logged but not stored (that needs a migration).
- **Tests:**
  - `tests/test_ai_provider_hardening.py` (backend);
  - `frontend/src/test/ExternalProcessing.test.tsx` (frontend);
  - adapted inputs in `tests/conftest.py` (Meridian authorizes external AI), `tests/test_ai_output_integrity.py` and `tests/test_real_pdf_ingestion.py` (the trigger helpers send the confirmation).

## 18. Stage 10A-3 — controlled live AI extraction harness (2026-10-18)

Operator guide: [AI_LIVE_TEST.md](AI_LIVE_TEST.md).

- **Added:** `scripts/ai_live_check.py`. It is disabled without a mode.
  - It drives the real extraction route and its gates (issuer authorization, 428 confirmation bound to the version, evidence check) in a temporary environment. `.env` is not loaded, and the database is refused unless it is in the temporary directory.
  - It accepts only the synthetic fixture with its pinned SHA-256.
  - **`--dry-run`:** zero requests.
  - **`--fake-provider`:** 14 offline scenarios, each with an expected outcome.
  - **`--live`:** two authorization flags, an explicit budget and the key from the process environment. A guard transport allows one `POST /v1/messages` and nothing else, and SDK retries stay disabled.
  - The worst-case cost is checked on the server's input size before confirming. Prices were verified on the pricing page (2026-10-18).
  - The structured report covers model, tokens, cost, claim counts, grounding, validation errors, timing and human review. It contains no document text; claim values go to the report file only, on request.
  - The harness never approves, and it checks that the development database is byte-identical afterwards.
- **Application:** `build_ai_service` (the `get_ai_service` factory with an injectable transport) and content-free usage metadata on the service (`last_exchange`, `requests_attempted`). No route, gate or schema changed.
- **Not done:** no live request has been made. Real AI extraction is not verified.
- **Tests:** `tests/test_ai_live_check.py` (offline; the live path runs with a fake transport while the real transport is impossible to construct).

## 19. Stage 10A-4 — preflight for the first live extraction (2026-10-18)

- **Authorized by the owner:** one request with `claude-sonnet-4-6` and the synthetic fixture, estimated budget USD 0.25, executed manually (AI_LIVE_TEST.md §6).
- **Model:** `claude-sonnet-4-6` is documented as active (legacy, retirement not sooner than 2027-02-17), at $3 / $15 per MTok (pricing page and model page, 2026-10-18). It is known to `anthropic` 1.14.0. It was missing from `SUPPORTED_AI_MODELS` and the harness price table, so the harness refused it; both now include it.
- **Budget:** worst case $0.1336 (3,562 input characters, 8,192 output tokens), within $0.25. It is an estimate, not a provider billing guarantee.
- **Added check:** the harness flags a response whose reported model differs from the requested one (`model_reported_differs_from_request`). No fallback is ever requested.
- **Not done:** the live request itself (by the owner, outside Claude Code). Real AI extraction is not verified.

## 20. Stage 10B-1 — real-browser end-to-end tests (2026-10-18)

Details: [E2E_TESTING.md](E2E_TESTING.md).

- **Added:**
  - Playwright (`@playwright/test` 1.63.0, exact) with Playwright's Chromium;
  - `scripts/e2e_run.py`: isolated servers on free ports, a temporary database, the real application, a production frontend build, offline network isolation, credential scan of the reports, and a byte check of the development database;
  - `scripts/e2e_seed.py`: a synthetic dataset seeded through the real API;
  - `frontend/e2e/`: journeys A–G and the negative journeys.
- **Gaps found:** listed in E2E_TESTING.md (no not-found page, no PDF download link, and others). They are reported there, not fixed in this stage.
- **Not covered:** visual design, accessibility audits, and browsers other than Chromium.

## 21. Stage 10B-2 — institutional user journey (2026-10-18)

Details: [E2E_TESTING.md](E2E_TESTING.md).

- **Interface fixed:**
  - a "Page not found" page;
  - a verified PDF download: saved only if its bytes match the version's SHA-256, with file name, size and pages shown;
  - the page reloads when credentials change, and the sidebar shows the server-confirmed identity, never the credential;
  - the AI status is stated, and the extraction action is disabled when AI is disabled;
  - each extraction is labelled AI or not AI;
  - the second approval ("Add Your Approval");
  - page titles, form labels and inline errors;
  - unconfirmed Stellar records and invalid ones are no longer presented as proof.
- **Backend:** one read-only route, `GET /ai/status` (`extraction:read`). It returns no secret and no key. No migration, and no change to access rules.
- **Browser tests:**
  - the serial Legal Passport journey: register, upload, download and hash, then claims, review, both approvals, passport and anchor;
  - the anchor states pending, invalid and evidence changed;
  - document integrity failures;
  - recovery after an API failure;
  - a report-hygiene self-test that fails on purpose.
- **Credentials in reports:** prevented at the source. Failure messages are redacted and matcher page snapshots dropped before anything is written; the scan then verifies.
- **Not complete:** with AI disabled and no manual claim entry, no claim can be created for a newly uploaded PDF. The browser journey reviews and approves seeded scripted claims of the same synthetic PDF. An upload-to-passport lifecycle on one new document has therefore not been exercised. Nothing here is a legal opinion or a claim of legal validity.

## 22. Stage 10B-3 — manual legal claim workflow (2026-10-18)

Details: [EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md) §13 and [E2E_TESTING.md](E2E_TESTING.md).

- **Added:** `POST /documents/{document_id}/manual-extractions` (`extraction:run`, `app/manual_claims.py`).
  - An authorized user of the issuer types legal claims from the current version of an uploaded PDF. No AI is involved.
  - Every quotation must be verified on its cited page before anything is stored. The approval's source-integrity check of the stored PDF also runs first.
  - The record is an ordinary draft labelled `source: manual`. Review, approval gates, separation of duties, passport and evidence manifest are reused unchanged.
  - The passport reports `value_origin: manual_entry` and `extraction_source: manual`.
  - No migration, and no change to the role permissions.
- **Interface:**
  - "Add Manual Claim" on each document, for roles with `extraction:run`. It shows the stored text of the selected page; pages without text cannot be chosen.
  - Clear refusal messages, without internal detail on server errors.
  - Manual records are labelled on the asset, review and passport pages. The passport says whether a value is attested by the issuer only or also by an independent legal reviewer.
  - Anchor transaction actions are offered by role.
- **Browser tests:**
  - the complete lifecycle on a newly uploaded synthetic PDF: manual claim, refusal of fabricated and wrong-page quotations, two distinct reviewers, passport compared with the backend, not anchored;
  - negative journeys for manual claims.
- **Security finding:** the report-hygiene self-test found an intermittent credential leak. A matcher's diff colour codes could split a value past the redaction. It is fixed at the source (colour codes are removed before redaction), and the scan now also checks colour-stripped content. The one pre-fix report that held a run's synthetic credential was removed.
- **Not done:** an author column for extraction records (needs a migration), and real AI extraction (still unverified). Nothing here is a legal opinion or establishes legal validity.

## 23. Stage 10C-1 — grant demo readiness (2026-10-18)

Details: [GRANT_DEMO.md](GRANT_DEMO.md). Nothing is deployed, and the application is not production-ready.

- **Added (backward compatible; defaults unchanged):**
  - `READ_ONLY_MODE`: every non-GET/HEAD/OPTIONS request is refused with 403 before authentication, for every credential;
  - `API_DOCS_ENABLED`;
  - `SYNTHETIC_DATA_NOTICE`;
  - the three flags on `/health`;
  - security headers on every API response (nosniff, DENY, no-referrer, no-store, a restrictive CSP and permissions policy).
- **Interface:**
  - a "How to read this application" explainer on the dashboard;
  - a showcase banner;
  - write actions hidden on a read-only server (the server enforces it);
  - the API Reference uses the configured API URL and says when the docs are not published.
- **Local demo:** `scripts/grant_demo.py`. It serves a read-only showcase or a writable sandbox of synthetic data on 127.0.0.1 only, and deletes it when stopped.
- **Tests:** `tests/test_showcase_policies.py`, the `showcase` browser environment, and `ShowcaseMode.test.tsx`.
- **Findings for deployment:**
  - npm advisories: 2 moderate in production dependencies, high and critical ones in dev tooling, which includes `vite preview`;
  - Python dependencies not audited (`pip-audit` needs installation approval);
  - in-memory rate limits; no TLS or HSTS locally;
  - SQLite and local storage;
  - no author column on extraction records.

## 24. Stage 10C-2 — deployment package and local rehearsal (2026-10-18)

Details: [DEPLOYMENT.md](DEPLOYMENT.md). Nothing is deployed, and the application is not production-ready.

- **Dependencies:**
  - updated with tests passing: FastAPI 0.120.4, Starlette 0.49.3, python-multipart 0.0.32, python-dotenv 1.2.4;
  - Python advisories (OSV): 71 → 61 distinct advisories (141 → 121 OSV records, which list most of them twice). The per-package table is in DEPLOYMENT.md §5. The remaining ones are in Starlette 1.x-only fixes, pypdf 6.x, pytest and pip;
  - a test proves that a read-only server never parses a PDF;
  - npm: no non-major fix exists; 2 moderate advisories in the production bundle (react-router, not reachable), the rest in dev tooling.
- **Added:**
  - `CLIENT_RATE_LIMIT_PER_MINUTE` and `AUTH_FAILURE_LIMIT_PER_MINUTE`, per address, with bounded memory and hashed addresses in logs;
  - `scripts/build_showcase.py`, a deployable bundle with a showcase-only registry. It includes the genuine Testnet anchor when the demo dataset is present, and contains no synthetic anchor records;
  - `deploy/rehearsal_proxy.py`, the `rehearsal` browser environment, and the proposed `deploy/Caddyfile` and `deploy/rwa-showcase-backend.service` (not tested: Caddy, systemd and Docker are not available);
  - a frontend without the external font, with hashed files under `/static/`.
- **Clean checkout:** the files a checkpoint commit would contain were copied alone. The backend, frontend, type checks, build and browser suites pass there.

---

*This document reports engineering findings only. It is not a legal opinion, security certification, or compliance attestation.*
