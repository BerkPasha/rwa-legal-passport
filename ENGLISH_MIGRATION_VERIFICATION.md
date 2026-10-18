# English Migration — Phase 2C Verification Report

**Date:** 2026-10-15
**Scope:** Final compatibility and regression verification of the English migration (Phases 2A, 2B-1, 2B-2) against the verified pre-migration backup
**Mode:** Verification only. No application code, test, database or configuration was changed. No paid AI or Stellar call was made. Nothing was committed, pushed or deployed.

**Backup used as reference:** `C:\Users\berkf\rwa-legal-passport-backups\pre-english-migration-20261015-201425\` (HEAD `07ad72f`, 110 files, restore re-verified in this phase)
**Evidence files:** `C:\Users\berkf\rwa-legal-passport-backups\english-migration-tools\phase2c-evidence\` (outside the repository; contains no credentials)

---

## Verdict

**No regression found.** Every check that could be run locally passed. Executable logic, database schema, routes, response structures, authorization rules and PDF security controls are identical to the pre-migration backup. The only changes are human-readable strings, comments and documentation. Twelve items are **NOT VERIFIED** because they need a paid service, an external party or a fault that cannot be produced safely (§13).

| # | Check | Result |
|---|---|---|
| 1 | Current application compared with the pre-migration backup | PASS |
| 2 | Executable logic unchanged (AST with all strings masked) | PASS |
| 3 | Database schema unchanged | PASS |
| 4 | Routes, methods, status codes, response models, security schemes | PASS |
| 5 | Authorization rules (per-route permission guards, role matrix, tenant scoping) | PASS |
| 6 | PDF security controls | PASS |
| 7 | Only intended human-readable runtime strings translated | PASS |
| 8 | All 131 Phase 2B-2 replacements reviewed; dependent tests not weakened | PASS |
| 9 | All 23 API operations verified | PASS (success paths of 3 operations NOT VERIFIED live; see §13) |
| 10 | Seven renamed test IDs; automation dependencies | PASS (no dependency found) |
| 11 | External clients matching old Turkish messages | RISK DOCUMENTED (§8); impact on clients outside the repository NOT VERIFIED |
| 12 | Backend tests, frontend tests, TypeScript check | PASS (280/280, 34/34, `tsc` exit 0) |
| 13 | Local live HTTP regression suites | PASS (37/37, 28/28, 29/29) + differential 112/112 + rate-limit probe |
| 14 | Database, credentials, backups and legal documents unchanged | PASS |
| 15 | Six deferred AI-language lines preserved | PASS |

---

## 1. Comparison with the pre-migration backup — PASS

The backup's `working_tree.zip` was extracted to a scratch folder. Every file was then compared with the current repository by SHA-256. `.git`, `.venv`, `node_modules`, `__pycache__`, `.pytest_cache` and `dist` were excluded.

| Result | Files |
|---|---:|
| Identical | 58 |
| Changed | 49 (47 Python files, `README.md`, `.env.example`) |
| Added | 1 (`ENGLISH_MIGRATION_PLAN.md`) |
| Removed | 0 |

All 32 frontend files are identical, as are `requirements.txt`, `frontend/package-lock.json`, `pytest.ini` and `start-rwa.bat`. So are the DB, the credentials, the access configuration, `.env` and the `backups/` databases.

The backup's own restore verifier (`verify_restore.py`) was re-run. It restored the bundle and archive into scratch, then deleted the copy:

| Check | Result |
|---|---|
| HEAD commit matches | PASS |
| All 110 restored files match manifest hashes | PASS |
| `uncommitted_changes.patch` reproduces the restored diff | PASS |
| SQLite `integrity_check` on all 3 databases | PASS |
| Restore copy (contains credentials) removed | PASS |
| "Git status identical to live repo" / "live repo unchanged since backup" | FAIL, expected: the migration modified 14 more tracked files and added the plan. These are exactly the 49 changed and 1 added files above |

## 2. Executable logic — PASS

For each of the 47 changed Python files, the AST of the backup version and the current version were compared with **every string constant masked** (`v2c_compare.py`). **All 47 are structurally identical.** This covers:
- every statement, expression, control flow, call and argument;
- every import, name, attribute, decorator, default value and f-string interpolation.

So the migration changed string text only: no logic, no identifiers, and no new or removed interpolated values.

**Comments.** Comments are not part of the AST, so machine-meaningful comments were compared separately (`# noqa`, `# type:`, `# pragma`, `# fmt:`, coding lines, shebangs). Only one differs: the explanation after `# noqa: BLE001` in `app/services/pdf.py` was translated. The directive itself is unchanged, so lint behavior is the same.

**Logs.** In the three changed log calls, event keys (`access_config.missing`, `legacy_credentials.ignored`) and `%s` placeholders are unchanged.

## 3. Database schema — PASS

The SQLAlchemy metadata of both trees was compiled to SQLite DDL. Both produce 8 tables and 18 `CREATE` statements, and the DDL is **identical**. The dev DB file `rwa_legal_passport.db` is byte-identical to the backup: SHA-256 `fe84578d885ca588…`, 8 tables, 19 indexes, `integrity_check` ok, all tables empty. `app/db/schema_check.py` is structurally identical; only its error-message text changed.

## 4. Routes, responses and security schemes — PASS

Both applications were imported and their contracts dumped (`v2c_contract.py`):

| Item | Result |
|---|---|
| Routes (28 incl. docs/root), methods, paths, success status codes, response models, endpoint functions | Identical |
| Full dependency tree per route (auth, permission guards, DB, storage, AI, Stellar, rate limiters) | Identical (after normalising object memory addresses) |
| Enums (10), settings fields and defaults, middleware stack | Identical |
| OpenAPI document with prose (`description`/`summary`/`title`) removed | Identical |
| Full OpenAPI document vs the post-2B-1 snapshot | Byte-identical (2B-2 changed no OpenAPI text) |
| Turkish text in current OpenAPI | None |

## 5. Authorization rules — PASS

- `app/permissions.py`, `app/authorization.py`, `app/auth.py`, `app/api_keys.py` and `app/access_registry.py` are structurally identical. Only their docstrings, comments and messages changed.
- The permission captured by every route guard is identical in both trees (table in §6).
- The live differential run (§10) confirmed identical status codes for:
  - every role denial (403);
  - wrong credential type (403);
  - invalid token or key (401);
  - cross-tenant access (404, with a body identical to a missing resource);
  - institutional scope (`passport:read`, out-of-scope asset gets the same 404 as an unknown asset).
- §5.1 invariant: "asset missing" and "asset out of scope" in `passport.py` are both `Asset not found.`. The live passport run confirmed identical bodies.

## 6. API operations — PASS

All 23 OpenAPI operations have the same method, path, success code, security scheme and permission guard in both trees. "Observed live" lists the statuses seen in the differential run, where each was identical on the backup and the current app.

| Method | Path | Success | Security | Guard | Observed live (current = backup) |
|---|---|---|---|---|---|
| `GET` | `/health` | 200 | public | — | 200 |
| `GET` | `/auth/me` | 200 | HTTPBearer | — | 200×5, 401, 403×2 |
| `POST` | `/assets` | 201 | HTTPBearer | asset:create | 201×2, 403×3, 409, 422×4 |
| `GET` | `/assets` | 200 | HTTPBearer | asset:read | 200×4, 403×2 |
| `GET` | `/assets/{asset_id}` | 200 | HTTPBearer | asset:read | 200×3, 404×4 |
| `PATCH` | `/assets/{asset_id}` | 200 | HTTPBearer | asset:update | 200×2, 403, 404, 422 |
| `POST` | `/assets/{asset_id}/documents` | 201 | HTTPBearer | document:upload | 201×3, 403×2, 404×2, 413, 415×2, 422×6 |
| `GET` | `/assets/{asset_id}/documents` | 200 | HTTPBearer | document:read | 200×3, 404 |
| `GET` | `/documents/{document_id}` | 200 | HTTPBearer | document:read | 200×3, 404×2 |
| `POST` | `/documents/{document_id}/versions` | 201 | HTTPBearer | document:upload | 201, 403, 404, 422 |
| `GET` | `/documents/{document_id}/versions` | 200 | HTTPBearer | document:read | 200, 404 |
| `GET` | `/documents/{document_id}/text` | 200 | HTTPBearer | document:read | 200, 404 |
| `GET` | `/documents/{document_id}/download` | 200 | HTTPBearer | document:read | 200×2, 403×2, 404, 500×2 |
| `POST` | `/documents/{document_id}/extractions` | 201 | HTTPBearer | extraction:run | 403×2, 404×2 (success path not run: paid AI) |
| `GET` | `/documents/{document_id}/extractions` | 200 | HTTPBearer | extraction:read | 200, 404 |
| `GET` | `/extractions/{extraction_id}` | 200 | HTTPBearer | extraction:read | 403, 404 |
| `POST` | `/extractions/{extraction_id}/review` | 200 | HTTPBearer | review:start | 403, 404 |
| `PATCH` | `/extractions/{extraction_id}/claims/{claim_id}` | 200 | HTTPBearer | claim:correct | 403, 404, 422×2 |
| `POST` | `/extractions/{extraction_id}/approvals` | 201 | HTTPBearer | approval:create | 403, 404×2 |
| `GET` | `/extractions/{extraction_id}/corrections` | 200 | HTTPBearer | audit:read | 404 |
| `GET` | `/extractions/{extraction_id}/approvals` | 200 | HTTPBearer | audit:read | 404 |
| `GET` | `/assets/{asset_id}/blockchain-status` | 200 | HTTPBearer | asset:read | 403×2, 404×2 (in-scope call not run: Horizon) |
| `GET` | `/passport/{network}/{asset_code}/{issuer_key}` | 200 | APIKeyHeader | passport:read | 401, 403×2, 404×5, 429 |

The success paths of extraction, review, correction, approval and passport `200` need an AI-produced extraction. They are covered by the 280 backend tests with the mock AI and mock Stellar services, not live (§13).

## 7. Only intended strings translated — PASS

There are 476 string constants changed since the backup. Every one is accounted for by phase:

| Phase | Count | Contexts |
|---|---:|---|
| 2A | 255 | Docstrings only |
| 2B-1 | 90 | OpenAPI `description` 33, `summary` 23, `responses` 6, tag metadata 8, CLI `print` 13, argparse `help` 4, log prose 3 |
| 2B-2 | 131 | Exception and `detail` messages (app); assertion, `match=`, parametrize, skip and mock text (tests) |

Changes in "sensitive" syntactic positions were also checked: dict keys, comparison operands, subscripts, `match` patterns, default arguments and class attributes. Only **8** were found. All 8 are test assertions that compare a response against the app's own message, which is the intended lockstep update.

**Not changed:**
- no dict key, enum value, field name, route path or header name;
- no permission, operation or log event key;
- no fixture or legal-evidence string.

**Remaining Turkish: 116 lines in 11 files.** Every one was checked line by line and is verbatim in the backup, apart from the 79 quotation lines in the new `ENGLISH_MIGRATION_PLAN.md`. No Turkish was introduced elsewhere. The current OpenAPI document and all live responses from the current app contain no Turkish.

## 8. Phase 2B-2 replacements and dependent tests — PASS

The exact pre-2B-2 state was rebuilt from the E01–E06 batch snapshots. Comparing it with the current tree gives **exactly 131 string constants in 27 files**, all structurally identical. Each pair (old, new, file, line, context) was reviewed by hand; the full list is in `phase2c-evidence\compare_2b2.json`. Every app-side change is the text of a raised exception, an `HTTPException.detail`, an error-message constant, or the `GET /` message.

**Lockstep check (no weakening).** For each of the 17 test literals that match app wording (comparisons, `pytest.raises(match=)`, parametrize values), I compared the set of runtime error messages it matched before with the set it matches now:

| Result | Literals |
|---|---|
| Same app messages before and after | 14 |
| **Stricter**: `at least one issuer_ids entry` now matches only the role message. The old Turkish substring also matched the institutional-client message | 1 |
| Equivalent: `match="already exists"` also matches the 409 asset message textually, but `pytest.raises(StorageError)` on `StorageService.save_new` can only see the storage message | 1 |
| Not statically decidable: the `document_versions index\(…\)` regex is matched against a message built at runtime. The schema-check test exercises it and passes | 1 |

**No assertion was weakened.** `test_passport.py:84` asserts `"approved"` together with `status_code == 404`. On the passport route, the only 404 message containing that word is `No approved legal passport is available for this asset yet.`.

**Execution coverage.** A `sys.monitoring` pytest plugin, kept outside the repository, recorded which of the 82 statements holding a translated app message run during the suite. **58/82** run. The other 24 were not run by the pre-migration suite either. They are listed in §13.

### Client-visible message changes (backward-compatibility risk)

These are the API responses whose text changed. **Status codes, headers and response structure are unchanged**; only the `detail` text, or `message` for `GET /`, is different. A client that matches the old Turkish text instead of the status code will stop matching. No such consumer exists in this repository: the frontend only displays `detail`, and no script or document matches on it. Clients outside the repository could not be verified.

| Status | Old (Turkish) | New (English) |
|---|---|---|
| 401 | Geçersiz veya eksik kimlik doğrulama token'ı. | Invalid or missing authentication token. |
| 403 | Bu işlem için yetkiniz yok. | You are not authorized to perform this operation. |
| 403 | X-API-Key başlığı gereklidir. | The X-API-Key header is required. |
| 401 | Geçersiz API anahtarı. | Invalid API key. |
| 403 | Bu API anahtarı bu işlem için yetkili değil. | This API key is not authorized for this operation. |
| 503 | Erişim yapılandırması geçersiz; sistem yöneticisine başvurun. | Access configuration is invalid; contact the system administrator. |
| 422 | issuer_id erişim yapılandırmasında kayıtlı bir ihraççıya ait değil. | issuer_id does not match an issuer registered in the access configuration. |
| 403 | Bu ihraççı için varlık kaydetme yetkiniz yok. | You are not authorized to register assets for this issuer. |
| 404 | Varlık bulunamadı. | Asset not found. |
| 404 | Belge bulunamadı. | Document not found. |
| 404 | Çıkarım bulunamadı. | Extraction not found. |
| 404 | Versiyon bulunamadı. / Belge versiyonu bulunamadı. | Document version not found. |
| 404 | İddia bulunamadı. | Claim not found. |
| 404 | Bu varlık için onaylanmış yasal pasaport henüz mevcut değil. | No approved legal passport is available for this asset yet. |
| 404 | Geçersiz blockchain ağı: {network!r}. | Invalid blockchain network: {network!r}. |
| 415 | Yalnızca PDF dosyaları (.pdf) kabul edilmektedir. | Only PDF files (.pdf) are accepted. |
| 415 | Dosya geçerli bir PDF gibi görünmüyor (magic byte uyuşmazlığı). | File does not appear to be a valid PDF (magic byte mismatch). |
| 413 | Dosya {max_mb} MB sınırını aşıyor. | File exceeds the {max_mb} MB limit. |
| 413 | İstek gövdesi izin verilen boyutu aşıyor. | Request body exceeds the maximum allowed size. |
| 400 | Geçersiz Content-Length başlığı. | Invalid Content-Length header. |
| 422 | Dosya boş. | File is empty. |
| 422 | PDF eksik veya kesilmiş görünüyor (%%EOF işareti yok). | PDF appears incomplete or truncated (no %%EOF marker). |
| 422 | Şifreli PDF dosyaları desteklenmiyor. | Encrypted PDF files are not supported. |
| 422 | PDF ayrıştırılamadı: dosya bozuk veya geçersiz. | PDF could not be parsed: the file is corrupt or invalid. |
| 422 | PDF hiç sayfa içermiyor. | PDF contains no pages. |
| 422 | PDF {n} sayfa sınırını aşıyor. | PDF exceeds the {n}-page limit. |
| 422 | PDF metni {n} karakter sınırını aşıyor. | PDF text exceeds the {n}-character limit. |
| 422 | PDF ayrıştırılamadı: sayfa {i} okunamadı. | PDF could not be parsed: page {i} is unreadable. |
| 422 | PDF işleme süre sınırını aştı. | PDF processing exceeded the time limit. |
| 422 | Onay reddedildi — şu alanlarda kaynak kanıt eksik: … | Approval rejected — missing source evidence for: … |
| 409 | Onaylanmış çıkarım yeniden incelemeye alınamaz. | An approved extraction cannot be returned to review. |
| 409 | Onaylanmış çıkarım düzeltilemez. | An approved extraction cannot be corrected. |
| 409 | Eşzamanlı bir yükleme aynı versiyonu oluşturdu; lütfen tekrar deneyin. | A concurrent upload created the same version; please retry. |
| 403 | AI servisi onay işlemi yapamaz. | The AI service cannot approve extractions. |
| 429 | İstek hız sınırı aşıldı. Lütfen bir dakika sonra tekrar deneyin. | Rate limit exceeded. Please try again in a minute. |
| 429 | AI çıkarım sınırı aşıldı. Lütfen daha sonra tekrar deneyin. | AI extraction rate limit exceeded. Please try again later. |
| 500 | Belge depolanamadı; lütfen tekrar deneyin. | Document could not be stored; please retry. |
| 500 | Belge bütünlük doğrulaması başarısız; dosya sunulmadı. | Document integrity check failed; the file was not served. |
| 500 | Dosya diskte bulunamadı. | Stored file not found. |
| 503 | Veritabanı meşgul; lütfen yüklemeyi tekrar deneyin. | Database is busy; please retry the upload. |
| 503 | AI servisi: … (4 messages, forwarded via `str(exc)`) | AI service: … / AI returned an invalid response format: … |
| 503 | Horizon … (3 messages, forwarded via `str(exc)`) | Horizon connection timed out / HTTP error / network error (…) |
| 200 | `GET /` message: "… — /docs adresini ziyaret edin." | "… — visit /docs." |

**Unchanged, so not a risk:**
- FastAPI and Pydantic built-in texts: "Not authenticated" and validation error lists.
- The `already English` 409 duplicate-asset message.

**Mitigation for external clients:** depend on the HTTP status code, which is unchanged, not on `detail` text. A machine-readable error code field would be a contract change and is out of scope here.

**Information exposure** is unchanged:
- Storage and integrity exceptions never reach the client. The response carries a generic `detail` and the log records only the exception type or an event key.
- `AccessConfigError` returns a generic 503.
- AI and Horizon errors were forwarded via `str(exc)` before the migration and still are, with the same interpolated values. This holds because the AST is identical.

## 9. Renamed test IDs — PASS

Seven parametrized IDs of `tests/test_access_registry.py::test_invalid_configs_are_rejected_without_echoing_values` changed. The parametrize values are the expected message substrings:

| Old ID suffix | New ID suffix |
|---|---|
| `<lambda>-benzersiz0` | `<lambda>-must be unique0` |
| `<lambda>-benzersiz1` | `<lambda>-must be unique1` |
| `<lambda>-bilinmeyen ihraççı_0` | `<lambda>-unknown issuer0` |
| `<lambda>-bilinmeyen ihraççı_1` | `<lambda>-unknown issuer1` |
| `<lambda>-en az bir issuer_ids veya asset_ids` | `<lambda>-at least one issuer_ids or asset_ids` |
| `<lambda>-en az bir issuer_ids` | `<lambda>-at least one issuer_ids entry` |
| `<lambda>-global kapsamlıdır` | `<lambda>-global scope` |

- **Collection:** 280 tests both before and after, and the mapping is one-to-one. The collected IDs are identical to `pytest_ids.post2b2.txt`, with the pre-existing random `garbage_body` parameter masked.
- **Automation dependencies:** none found.
  - The repository has no CI configuration (`.github`, `*.yml`, Makefile, tox, nox or pre-commit).
  - `pytest.ini` has no selection.
  - No script or document uses `-k`, `--deselect` or node IDs.
  - There are no collection hooks in `tests/`.
  - `start-rwa.bat` does not run tests.
- **Local cache:** `.pytest_cache` is stale. It dates from Stage 9A, has 86 entries and contains none of these IDs.

## 10. Test suites and live HTTP regression — PASS

| Suite | Result |
|---|---|
| Backend `pytest` | **280 passed**, exit 0; IDs identical to the post-2B-2 list |
| Frontend `vitest` | **34 passed** (5 files); IDs identical to the Phase 1 baseline |
| TypeScript `tsc --noEmit` | exit 0, no output |
| `verify_local_setup.py` (`:8000` dev DB read-only, `:5173` Vite, `:8001` scratch) | **37/37** |
| `verify_2b2_messages.py` (exact English details, status codes, no Turkish, no secret echo, cross-tenant = missing) | **28/28** |
| `verify_9b2_live.py` (real uploads/downloads, 9A exploit payloads, rejected uploads, tamper detection, canary outside storage) | **29/29** |
| **Differential run** `v2c_differential.py`: same 112 requests to the current app (`:8001`) and the **unmodified backup app** (`:8002`), each on its own scratch copy of the dev DB | **112/112 equivalent** |
| Rate-limit probe (passport, both servers) | Same 429 status and headers; detail is the recorded translation; both limit at 60/min |

**How the differential run compares.** Status codes and all headers had to be identical, except `date`, `server` and `content-length`. Response bodies were normalised for UUIDs and timestamps:
- 61 bodies were identical.
- 51 differ only in `detail` or `message`. Each of those equals the backup's text after the recorded 2B-2 substitutions are applied (single pass, exact match).
- Every current-app response was checked for Turkish characters and for echoed credentials: none.

**Coverage of the differential run:**
- every role and tenant denial;
- every PDF rejection: wrong type, magic bytes, empty, truncated, encrypted, zero pages, corrupt, oversized;
- path-traversal filenames;
- integrity failure on a tampered stored file, and a missing stored file;
- CORS allow and deny;
- passport scope;
- a malformed `Content-Length`. Uvicorn rejects this itself before the application sees it, identically on both servers.

No in-scope AI extraction or blockchain-status request was sent. All servers were stopped afterwards; ports 8000, 8001, 8002 and 5173 are free. Server logs contain no Turkish text and no tracebacks; the only 5xx responses are the deliberate integrity tests.

**Method notes.**
- On its first run the differential harness reported 7 false failures. Two harness bugs caused them:
  - The substitution pass re-applied short f-string fragments, such as `'PDF '`, to text that was already English.
  - The `GET /` text lives in a `message` field, not `detail`.

  Both bugs were fixed in the harness only, and the run was repeated on fresh scratch copies.
- The lockstep check initially compared against every app string, docstrings included. It was narrowed to runtime error messages, and the remaining cases were resolved by inspection (§8).

## 11. Database, credentials, backups and legal documents — PASS

| Item | Result |
|---|---|
| `rwa_legal_passport.db` | SHA-256 equals the backup manifest; `integrity_check` ok; only read-only (`mode=ro`) connections were opened |
| `secrets/dev_credentials.json`, `access_control.json`, `.env` | SHA-256 equal to the backup manifest; `secrets/` ACL restricted to the owning user |
| `backups/…pre-9b1….db`, `backups/…pre-9b2….db` | SHA-256 equal to the backup manifest; `integrity_check` ok |
| Pre-migration backup folder | Restore re-verified (§1); bundle verifies; zip has 110 entries, none corrupt; ACL restricted to the owning user |
| Uploaded legal documents | None exist in the dev environment: the default `./storage` is absent and the DB has 0 rows. Nothing to alter |
| Legal-evidence and multilingual test fixtures (D4) | All 20 lines are verbatim from the backup; no fixture or data string in any test file changed |
| Protected-file fingerprints vs Phase 1 baseline | Unchanged |
| Git | HEAD `07ad72f` on `main`, 2 commits, nothing staged, no stash; `main` level with `origin/main`; no commit, push or deployment |

## 12. Deferred AI-language lines — PASS

The six Turkish lines in `app/services/ai.py` are verbatim from the backup:
- `UNVERIFIED_DISCLAIMER`, lines 28–31;
- the empty-document text, line 81;
- the page-marker labels in `_format_pages`, line 83.

`_SYSTEM_PROMPT` is unchanged. Their translation remains deferred (D1: new records in a later batch; D3: Stage 9B-4).

## 13. NOT VERIFIED

| # | Item | Why | Existing coverage |
|---|---|---|---|
| 1 | AI extraction success path and the 4 AI error messages (503) | Requires a paid AI call | Success path covered by pytest with the mock AI; error texts reviewed statically (literal-only change, structure identical) |
| 2 | In-scope `blockchain-status` and the 3 Horizon error messages (503) | Requires a Stellar Horizon call | Success path covered by pytest with the mock Stellar service; texts reviewed statically |
| 3 | Live review, correction, approval and approved-passport `200` flows | Need an AI-produced extraction | Covered by pytest (`test_review.py`, `test_passport.py`) |
| 4 | Clients outside this repository that match Turkish `detail` text | Not observable from here | Documented in §8 |
| 5 | "Invalid Content-Length header." (400, middleware) | Unreachable through uvicorn, which rejects the request first; not run by tests | Static review |
| 6 | "Document version not found." (3 sites), "A concurrent upload created the same version" (409), "Database is busy" (503 ×2), "Document could not be stored" (500) | Need DB inconsistency, an upload race or a filesystem failure; not run by tests | Static review |
| 7 | "PDF processing exceeded the time limit." at `documents.py:157` (async timeout around parsing) and `pdf.py:113` | Need a PDF that exceeds the parse budget at those points | The third site, `pdf.py:97`, runs and is asserted by `test_parse_time_budget_is_enforced` |
| 8 | "PDF could not be parsed: page {i} is unreadable." and the storage `File could not be created/read (…)` messages | Need a page-level parser failure or an OS error | Static review |
| 9 | "You are not authorized to register assets for this issuer." (`assets.py:41`) and the defensive role check in `review.py:72` | Unreachable with the current role matrix: only the global `platform_admin` holds `asset:create`, and every holder of a review permission has a reviewer role | Static review |
| 10 | "access configuration could not be read: …" | Needs an unreadable config file | Static review |
| 11 | Visual rendering of English errors in a browser | No browser or E2E harness | vitest covers `ApiError` behaviour |
| 12 | Production build output (`frontend/dist`) | No build run in this phase; no frontend file changed | `tsc` exit 0 |

**The 24 statements the test suite doesn't run (§8):**

| Items | Statements |
|---|---:|
| 1 (AI) | 4 |
| 2 (Horizon) | 3 |
| 5 | 1 |
| 6 | 7 |
| 7 | 2 |
| 8 | 3 |
| 9 | 2 |
| 10 | 1 |
| Invalid blockchain network (`passport.py:102`), verified live by the differential run (§10) | 1 |

**These statements were not run by the pre-migration suite either**, so the coverage gap is unchanged. For every one, the change is literal text only and the AST structure is identical.

## 14. Observations (pre-existing, not regressions)

1. The `ai.py` invalid-response message still forwards `{exc}` to clients (Stage 9B-6).
2. `PDF appears incomplete or truncated (no %%EOF marker).` keeps the literal `%%`, exactly as before.
3. `PATCH /assets/{id}` with an immutable field (`stellar_asset_code`) returns 200 and ignores the field, identically before and after.
4. A test parameter in `test_invalid_pdfs_are_rejected_without_side_effects[garbage_body-…]` is random, so its test ID changes on every run.
5. `tests/test_blockchain.py:135` keeps a Turkish-keyword fallback in an assertion about the already-English blockchain disclaimer.
6. 24 of the 82 translated message statements are not run by any test (§8, §13). Adding tests is outside this phase.
7. D6 (a narrow English regression check) is still open. Nothing currently prevents Turkish from being reintroduced.

## 15. Reproduction

All tools are outside the repository, in `C:\Users\berkf\rwa-legal-passport-backups\english-migration-tools\`:

| Tool | Purpose |
|---|---|
| `v2c_compare.py <pre_tree> <cur_tree> <out.json>` | File hashes, masked-AST identity, every changed string with its syntactic context |
| `v2c_lockstep.py <pre_tree> <cur_tree> <compare.json> --runtime-only` | Test literals vs the app messages they match, before and after |
| `v2c_contract.py <tree> <out.json>` (cwd = tree) | DDL, routes, dependency guards, enums, settings, OpenAPI |
| `v2c_differential.py <compare.json> <storageA> <storageB>` | Same request sequence against `:8001` (current) and `:8002` (backup) |
| `phase2c-evidence\v2c_linehits.py` | pytest plugin (`-p v2c_linehits`, env `V2C_TARGETS`, `V2C_HITS`) recording executed message statements |
| `verify_local_setup.py`, `verify_2b2_messages.py`, `verify_9b2_live.py` | Existing live suites |
| `pre-english-migration-…\verify_restore.py` | Backup restore verification |
