# Access Control

Introduced in **Stage 9B-1**. This document is the reference for who can call what. The route table in §3 is enforced by `tests/test_access_control.py::test_route_inventory_matches_expected_guards`, so any new route fails the test suite until it gets an explicit authorization decision.

## 1. Credential types

| Credential | Header | Who | Accepted on |
|---|---|---|---|
| User token | `Authorization: Bearer <token>` | Platform operators and reviewers | Every internal route |
| Institutional API key | `X-API-Key: <key>` | External institutions | `GET /passport/...` only |

The two types are not interchangeable. An API key sent to an internal route gets `403`, and so does a user token sent to the passport route.

Response rules:

| Situation | Status |
|---|---|
| Credential header missing | `403` |
| Unknown or disabled token / key | `401` (`WWW-Authenticate: Bearer` for user tokens) |
| Role lacks the permission, or key lacks the operation | `403` |
| Resource belongs to an issuer outside the caller's scope | `404`, with the same body as a non-existent resource |
| Access configuration file present but invalid | `503` at runtime; the app refuses to start |

## 2. Roles and permissions (`app/permissions.py`)

Each asset belongs to exactly one **issuer** (`assets.issuer_id`, set at registration and immutable). Documents, versions, extractions, corrections and approvals inherit that issuer through the asset.

| Permission | `platform_admin` (global) | `issuer_reviewer` (own issuers) | `independent_legal_reviewer` (assigned issuers) |
|---|:-:|:-:|:-:|
| `asset:create` | ✓ | — | — |
| `asset:read` | ✓ | ✓ | ✓ |
| `asset:update` | ✓ | — | — |
| `document:upload` | ✓ | ✓ | — |
| `document:read` | ✓ | ✓ | ✓ |
| `extraction:run` (paid AI call, rate-limited) | ✓ | ✓ | — |
| `extraction:read` (includes drafts) | ✓ | ✓ | ✓ |
| `review:start` | — | ✓ | ✓ |
| `claim:correct` | — | ✓ | ✓ |
| `approval:create` | — | ✓ → `issuer_attestation` | ✓ → `legal_verification` |
| `audit:read` (corrections, approvals) | ✓ | ✓ | ✓ |

- The approval type is always derived from the authenticated role; a request cannot choose it. An issuer reviewer can therefore never produce a `legal_verification`.
- The platform operator manages the registry and can read for support and audit purposes, but cannot review, correct or approve.
- A user ID starting with `ai-` can never approve, as an extra guard. The AI service itself has no credential.

Institutional keys carry **operations** (currently only `passport:read`) and a scope of `issuer_ids` and/or `asset_ids`. A key reads a passport only when the asset's issuer is in `issuer_ids` or the asset ID is in `asset_ids`.

## 3. Route inventory

| Method | Route | Guard | Tenant check |
|---|---|---|---|
| GET | `/health` | public | — |
| GET | `/ready` | public (readiness probe; answers only `ready` / `not_ready`) | — |
| GET | `/auth/me` | any user token | — |
| POST | `/assets` | `asset:create` | `issuer_id` must exist in the registry and be in scope |
| GET | `/assets` | `asset:read` | list filtered to the caller's issuers |
| GET | `/assets/{asset_id}` | `asset:read` | asset issuer |
| PATCH | `/assets/{asset_id}` | `asset:update` | asset issuer (`issuer_id` not updatable) |
| GET | `/assets/{asset_id}/blockchain-status` | `asset:read` | asset issuer |
| POST | `/assets/{asset_id}/documents` | `document:upload` | asset issuer |
| GET | `/assets/{asset_id}/documents` | `document:read` | asset issuer |
| GET | `/documents/{document_id}` | `document:read` | document → asset issuer |
| POST | `/documents/{document_id}/versions` | `document:upload` | document → asset issuer |
| GET | `/documents/{document_id}/versions` | `document:read` | document → asset issuer |
| GET | `/documents/{document_id}/text` | `document:read` | document → asset issuer |
| GET | `/documents/{document_id}/versions/{version_number}/text` | `document:read` | document → asset issuer; a foreign document answers 404 for every version number (Stage 9B-4) |
| GET | `/documents/{document_id}/download` | `document:read` | document → asset issuer |
| POST | `/documents/{document_id}/extractions` | `extraction:run` + per-user rate limit; with an external AI provider also the issuer's `external_ai_processing` and a per-request confirmation ([AI_EXTRACTION.md](AI_EXTRACTION.md)) | document → asset issuer |
| POST | `/documents/{document_id}/manual-extractions` | `extraction:run` (manual claims, no AI; Stage 10B-3, [EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md) §13) | document → asset issuer; `asset_id` must be the document's asset |
| GET | `/documents/{document_id}/extractions` | `extraction:read` | document → asset issuer |
| GET | `/ai/status` | `extraction:read` | — (server-wide: whether AI extraction is available; no key or secret, Stage 10B-2) |
| GET | `/extractions/{extraction_id}` | `extraction:read` | extraction → document → asset issuer |
| POST | `/extractions/{extraction_id}/review` | `review:start` | extraction → … → issuer |
| PATCH | `/extractions/{extraction_id}/claims/{claim_id}` | `claim:correct` | extraction → … → issuer |
| POST | `/extractions/{extraction_id}/approvals` | `approval:create` | extraction → … → issuer |
| GET | `/extractions/{extraction_id}/corrections` | `audit:read` | extraction → … → issuer |
| GET | `/extractions/{extraction_id}/approvals` | `audit:read` | extraction → … → issuer |
| GET | `/passport/{network}/{asset_code}/{issuer_key}` | API key with `passport:read` + 60 req/min | key's `issuer_ids` / `asset_ids` |

Not in the table: `/` (static welcome message) and FastAPI's `/docs`, `/redoc` and `/openapi.json`, which are still public. See §7.

## 4. Configuration file

`ACCESS_CONTROL_FILE` (default `./access_control.json`, gitignored). Template: `access_control.example.json`.

```json
{
  "issuers": [{ "id": "acme-holdings", "name": "Acme Holdings LLC", "external_ai_processing": false }],
  "users": [
    { "user_id": "ops-admin-01", "display_name": "Operator", "role": "platform_admin",
      "token_sha256": "<64 hex>" },
    { "user_id": "acme-reviewer-01", "display_name": "Acme Reviewer", "role": "issuer_reviewer",
      "issuer_ids": ["acme-holdings"], "token_sha256": "<64 hex>" }
  ],
  "institutional_clients": [
    { "client_id": "fund-001", "organization": "Example Fund", "key_sha256": "<64 hex>",
      "issuer_ids": ["acme-holdings"], "asset_ids": [], "operations": ["passport:read"] }
  ]
}
```

Validation rejects the following, failing closed:
- unknown fields, including a plaintext `token` field
- references to undefined issuers, and wildcards such as `*`
- reviewers with no issuers, or a `platform_admin` with `issuer_ids`
- duplicate IDs or hashes, including the same hash on a user and a client
- clients with no scope or no operations

Validation messages never include input values.

`external_ai_processing` (optional, default `false`, JSON `true`/`false` only): the issuer has authorized sending the text of its documents to the configured external AI provider (Stage 10A-2, [AI_EXTRACTION.md](AI_EXTRACTION.md)). Without it, extraction with an external provider is refused (403) and nothing is sent.

Operational behaviour:
- **Missing file:** the registry is empty and every protected route denies; a warning is logged.
- **Hot reload:** the file reloads when it changes. Setting `"enabled": false` revokes a credential without a restart.
- **Startup:** the app refuses to start if the file exists but is invalid.

## 5. Managing credentials

```powershell
python -m app.access_tool new-token          # prints a new token (once) and its SHA-256
python -m app.access_tool hash               # hash a token typed at a hidden prompt
python -m app.access_tool hash-env NAME      # hash a value from .env / environment without printing it
python -m app.access_tool check              # validate the access file; prints counts only
```

Deliver the token to its holder over a secure channel, and store only the hash in the file. Tokens should be at least 32 random bytes; `new-token` generates 32-byte URL-safe tokens.

**Migrating from pre-9B-1 `.env` tokens.** `REVIEWER_ISSUER_TOKEN`, `REVIEWER_LEGAL_TOKEN`, `INSTITUTIONAL_API_KEY_ALPHA` and `INSTITUTIONAL_API_KEY_BETA` no longer authenticate anything; startup logs a warning while they are still set. To keep an existing value, run `python -m app.access_tool hash-env REVIEWER_ISSUER_TOKEN`, put the hash on the matching user entry with the right role and issuers, then delete the variable from `.env`.

**Database.** `assets.issuer_id` is a new required column. On a database created before 9B-1, the app refuses to start and lists the missing columns. It never alters data. Back up and recreate an empty dev database, or migrate existing rows by assigning each asset to its issuer.

## 6. CORS and browser credentials

- `CORS_ALLOWED_ORIGINS` takes comma-separated `http(s)://host[:port]` values (default `http://localhost:5173,http://127.0.0.1:5173`). `*`, wildcards, paths and malformed values are rejected at startup.
- Allowed methods are `GET`, `POST`, `PATCH` and `OPTIONS`. Allowed headers are `Authorization`, `Content-Type` and `X-API-Key`. `allow_credentials=False`, since there is no cookie auth.
- The frontend keeps credentials in `sessionStorage` for the current tab only, and purges anything older versions stored in `localStorage`. The user token is never sent to the passport endpoint.

## 7. Known limitations (tracked for later 9B/9C steps)

- Static, long-lived tokens with no expiry. Production should move to OIDC/JWT; `Principal` and `require_permission()` are designed to stay as they are.
- Rate limiters are in-process memory: they reset on restart and are not shared across workers.
- `/docs`, `/redoc` and `/openapi.json` are public by default. They expose the API shape but no data. Since Stage 10C-1, `API_DOCS_ENABLED=false` turns them off (404), and `READ_ONLY_MODE=true` refuses every write request for any credential ([GRANT_DEMO.md](GRANT_DEMO.md)).
- Multipart bodies are still parsed before authentication runs. Since 9B-2, a request-size cap (`MAX_UPLOAD_SIZE_MB` + 64 KiB) rejects oversized bodies before they are buffered.
- Not covered by access control: issuer-only approvals still producing a served passport (decision 1, step 9B-5). Stale approvals were addressed in 9B-3 (`APPROVAL_INTEGRITY.md`), passage verification in 9B-4 (`EVIDENCE_VERIFICATION.md`), and upload path safety in 9B-2 (`PRODUCT_READINESS.md` §10).
