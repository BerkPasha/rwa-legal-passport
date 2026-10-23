# Architecture

RWA Legal Passport is one Python backend and one React codebase. The backend owns the legal record and every integrity rule. The frontend is a review application for issuers and legal reviewers, and it also builds the public website with a read-only demo.

```
 Reviewers (Bearer token)        Institutions (X-API-Key)        Website visitors
          │                                │                             │
          ▼                                ▼                             ▼
 ┌─────────────────────────┐      GET /passport/…          ┌──────────────────────────┐
 │ React review app (Vite) │──────────┐   (read-only)      │ Public site + /demo       │
 └───────────┬─────────────┘          │                    │ Vercel · static · no API  │
             │ REST / JSON            │                    │ bundled synthetic data    │
             ▼                        ▼                    └──────────────────────────┘
 ┌──────────────────────────────────────────────────────┐
 │ FastAPI backend (one uvicorn process)                │
 │  routers → integrity rules → SQLAlchemy (async)      │──► Horizon, Stellar Testnet
 │  SQLite database · PDF storage (local files)         │    (anchor submission + verification)
 │                                                      │──► Anthropic API (optional, off by default)
 └──────────────────────────────────────────────────────┘
```

## Backend

| Area | Modules | Responsibility |
|---|---|---|
| Startup | `app/main.py`, `app/deployment_checks.py`, `app/db/schema_check.py` | Refuse unsafe production settings, an invalid access registry or an out-of-date database schema before serving |
| HTTP | `app/routers/` | `assets`, `documents` (versions, page text, download), `extractions` and `review` (claims, corrections, approvals), `passport`, `stellar_anchors`, `auth`, `health` (`/health`, `/ready`) |
| Request guards | `app/middleware.py`, `app/rate_limit.py` | Body-size limit, optional read-only mode, API docs gate, security headers, per-client and failed-authentication limits |
| Access control | `app/access_registry.py`, `app/auth.py`, `app/api_keys.py`, `app/permissions.py`, `app/authorization.py` | Token and key digests, roles, permission matrix, issuer (tenant) scope. A resource outside the caller's scope answers 404 |
| Documents | `app/services/pdf.py`, `app/services/storage.py` | PDF validation and per-page text extraction within page, size and time limits; write-once storage verified by SHA-256 |
| Evidence | `app/evidence.py`, `app/evidence_records.py`, `app/correction_rules.py` | Deterministic quotation check against the stored text of the cited page; evidence rules for reviewer corrections |
| Approvals | `app/approval_integrity.py`, `app/source_integrity.py` | Binding written at approval time; currency evaluated on every read; stored file and page text re-checked at approval |
| Evidence manifest | `app/evidence_manifest.py` | Canonical JSON of exactly what the passport serves, and its SHA-256 digest |
| Stellar | `app/anchoring.py`, `app/services/stellar_anchor.py`, `app/services/stellar.py` | Testnet transaction building and submission, idempotency, re-checks and verification through Horizon |
| AI (optional) | `app/services/ai.py` | Anthropic extraction behind a server setting, a per-issuer opt-in and a per-request confirmation; output is always an unverified draft |

### Data model

```
assets ──< documents ──< document_versions ──< document_pages
                              │  (sha256, page_count)   (page text)
                              └──< legal_extractions ──< legal_claims
                                        │                   (value, page, quotation, check status)
                                        ├──< review_corrections
                                        └──< review_approvals   (binding + binding_digest)
assets ──< stellar_anchors   (manifest JSON, digest, transaction hash, ledger, status)
```

Approvals are never edited or deleted, and a new document version is a new row: the history is the rows themselves. Each new manifest digest gets its own anchor row. Only a pending anchor's status changes, to confirmed or failed, when it is re-checked on the network.

## The evidence chain

Each link is a hash or an exact reference to the previous one:

1. **Document version → file:** `document_versions.sha256_hash` is the SHA-256 of the stored PDF. Each page's text is stored with it.
2. **Claim → page:** a claim names a page and quotes it. The quotation must appear in that page's stored text. The check status, the page-text hash and the quotation hash are recorded.
3. **Approval → claims and version:** the approval binding holds the document version and its hash, every claim and its check result, and the asset's material information. `binding_digest` is the SHA-256 over all of it. An approval stops applying, without being changed, if any bound element changes. Examples are `document_version_superseded`, `evidence_changed` and `asset_information_changed`; the full list is in [APPROVAL_INTEGRITY.md](../APPROVAL_INTEGRITY.md).
4. **Passport → manifest:** the passport serves only claims with current approvals. The manifest is the canonical JSON of that selection (asset, evidence, counted approvals). It holds no reviewer identities, credentials or files.
5. **Manifest → Stellar:** the manifest digest is written as the transaction's `MEMO_HASH` and as a `manage_data` value (`lp:<asset id>`). The signer is a dedicated Testnet anchoring account. Only 32 bytes go on-chain; the manifest stays off-chain.
6. **Verification:** the result is `verified` only if all of these hold:
   - Horizon is on Testnet;
   - the transaction exists, succeeded and comes from the anchoring account;
   - its memo and operation carry the digest;
   - the stored manifest hashes to that digest;
   - the passport's current manifest has the same digest.

   Any other outcome is reported as what it is.

A verified anchor shows that this exact evidence digest was recorded at a ledger time and that the record is unchanged since. It does not show legal validity, ownership, compliance or document authenticity.

## Frontend

React 18, TypeScript, Vite 6, Tailwind CSS and React Router.

- `npm run build`: the review application (asset registry, documents and versions, claims review, approvals, Legal Passport lookup, Stellar evidence). It uses the API at `VITE_API_URL`. Credentials are kept for the browser tab only.
- `npm run build:public`: the public website (`frontend/src/PublicSite.tsx`) with the read-only demo at `/demo` (`frontend/src/demo/`). It contains no API client, no credential handling and no backend request. The demo renders a bundled snapshot of the LPDEMO record plus two illustrative synthetic records. This build is what Vercel serves, with a strict Content-Security-Policy (`frontend/vercel.json`).

## Deployment today

| Part | Where it runs |
|---|---|
| Website and read-only demo | Vercel (production branch `checkpoint/stage-9b4`), static files only |
| FastAPI backend, database, PDF storage | Local machine; not hosted |
| Stellar | Public Stellar Testnet through `horizon-testnet.stellar.org`; Mainnet is refused |

A reverse-proxy and service configuration for a read-only showcase server, together with its startup checks, is prepared in `deploy/` and [DEPLOYMENT.md](../DEPLOYMENT.md). It has not been run on a real host.

## Testing

- **Backend:** `pytest`. It uses in-memory or scratch databases, mocks the AI provider and guards the Stellar transport so no test reaches the network.
- **Frontend:** Vitest and Testing Library.
- **End to end:** Playwright with Chromium against a real backend and a production frontend build, on a disconnected computer ([E2E_TESTING.md](../E2E_TESTING.md)).
