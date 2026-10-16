# Legal Approval Version Integrity (Stage 9B-3)

This document describes how RWA Legal Passport keeps approvals tied to exactly what a reviewer approved. Outdated approvals are never presented as currently approved. The implementation is in `app/approval_integrity.py`; the regression tests are in `tests/test_approval_integrity.py`.

> **Updated for Stage 9B-4 (2026-10-16).** Since Stage 9B-4, approvals also bind the result of the quotation check (evidence snapshot format 2), every known claim must have a verified quotation before it can be approved, and three currency reasons were added. Statements of Stage 9B-3 that no longer hold are marked *superseded* below. The full evidence rules are in [EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md).

---

## 1. What an approval is bound to

When a reviewer approves an extraction (`POST /extractions/{id}/approvals`), the approval record stores a **binding**. The binding is written once and never updated.

| Binding field | Content |
|---|---|
| `document_id`, `document_version_id`, `document_version_number` | The exact document version the extraction was made from |
| `document_sha256` | SHA-256 of that version's stored file |
| `evidence_snapshot`, `evidence_digest` | Every claim of the extraction (field, value, unknown flag, source page, supporting passage) as canonical JSON, and its SHA-256. Since Stage 9B-4: format 2, which adds per claim the quotation-check status, the SHA-256 of the cited page text and of the quotation, and the correction behind the claim, plus the SHA-256 of the version's whole page text (`EVIDENCE_VERIFICATION.md` §3.2) |
| `asset_snapshot`, `asset_snapshot_digest` | Asset identifiers and material information (issuer, network, Stellar code and key, organization name, asset type, jurisdiction, status), and its SHA-256 |
| `asset_material_revision` | The asset's material revision at approval time |
| `binding_digest` | SHA-256 over the whole binding, including approval id, type, reviewer and timestamp |

The binding is captured while the request holds the database write lock, so all parts describe the same moment.

## 2. When an approval is current

Approval records are **never modified or deleted**. Whether an approval is *current* is evaluated on every read by comparing its binding with the live state. An approval is current only if none of the following reasons applies:

| Reason | Meaning |
|---|---|
| `unbound_legacy_approval` | The approval was created before Stage 9B-3 and has no binding |
| `binding_integrity_failed` | A stored snapshot or the binding no longer matches its digest (tampering or corruption) |
| `re_review_in_progress` | The extraction was reopened for review |
| `document_not_active` | The document is not active |
| `document_version_superseded` | A newer version of the document exists |
| `document_hash_mismatch` | The approved version's recorded SHA-256 differs from the binding |
| `evidence_changed` | The extraction's claims differ from the approved evidence |
| `asset_information_changed` | Material asset information changed (revision or snapshot differs) |
| `asset_not_active` | The asset is suspended or retired |
| `source_text_changed` | **Stage 9B-4:** the stored page text of the approved version no longer matches the approved hash |
| `evidence_not_verified` | **Stage 9B-4:** a known claim's quotation no longer appears on its cited page |
| `source_file_integrity_failed` | **Stage 9B-4, passport only:** the stored PDF is missing or no longer the approved bytes; the document is excluded from the passport |

`GET /extractions/{id}` and `GET /documents/{id}/extractions` return an `approval_state` for every extraction:

| State | Meaning |
|---|---|
| `not_approved` | No approval record exists |
| `current` | At least one approval is current |
| `stale` | Approvals exist but none is current; the document version is still current, so a re-review can restore currency |
| `superseded` | Approvals exist but a newer document version exists; only a new extraction on the current version can be approved |

`version_is_current: false` marks an extraction of a superseded version. Such an extraction can never be approved.

## 3. Rules

1. **Exact version.** Only an extraction of the document's current version can be approved. Otherwise the response is 409.
2. **Nothing is transferred.** A new document version starts without approvals, and approvals are never copied or moved.
3. **Material asset changes.** Every change of organization name, asset type, jurisdiction or status increments `assets.material_revision`. A PATCH that leaves every value unchanged does not. A changed revision makes every approval of the asset stale. Restoring the previous values is a new change and does not restore old approvals.
4. **Re-review before re-approval.** An approved extraction whose approvals are all stale can't simply be approved again (409). A reviewer first starts a re-review (`POST /extractions/{id}/review`), which is allowed only when no approval is current and the version is current. Corrections are then possible, and a new approval creates a new binding. Earlier approvals stay in the history.
5. **Active assets only.** Approvals are not accepted while the asset is suspended or retired. Reactivation is a material change, so a re-review is required.
6. **Issuer attestation is not independent verification.** The passport reports:
   - `verification_level`: `issuer_attested`, `partially_independently_verified` or `independently_verified`;
   - per field, `independently_verified`, which is true only when every source has a current `legal_verification` approval.

   *Superseded by Stage 9B-4:* `verified` no longer means only that source evidence exists. It now means the value is known, not conflicting, and its quotation is found on its cited page in every approved source. `independently_verified` additionally requires that. `verification_level` is derived from the verified fields only, and its floor `issuer_attested` is also used when no field is verified (`verification_summary` has the counts). None of these is a legal opinion.
7. **Several documents.** Each document contributes its current approved extraction. Within one document version, the most recently approved extraction governs. Across documents, if approved documents state different values for a field (compared ignoring whitespace and case), the field is a conflict: `value: "conflicting"`, `verified: false`, and every document's value listed in `sources`. Documents whose approval is no longer current are listed in `excluded_documents` with the reasons.
8. **Concurrency.** Approval, re-review and correction take the SQLite write lock before reading and checking the state. A concurrent upload, asset change or correction is therefore either completely before the check (and seen by it) or completely after the write (and makes the approval superseded or stale). In addition, currency is evaluated on every read, so no race can make an outdated approval appear current.

## 4. Institutional passport (`GET /passport/{network}/{asset_code}/{issuer_key}`)

| Situation | Response |
|---|---|
| Asset unknown or outside the key's scope | 404 `Asset not found.` (unchanged; staleness is not revealed) |
| Asset suspended or retired | 409 `The asset is not active; no current legal passport is available.` |
| Approvals exist but none is current | 409 `The legal approval for this asset is no longer current; a re-review is required.` |
| Never approved | 404 `No approved legal passport is available for this asset yet.` (unchanged) |
| At least one current approval | 200, built only from current approvals |

A 409 body contains only `detail`. No stale value is returned.

New 200 fields, all additive:
- `asset.status`;
- `verification_level`;
- `legal_rights[].independently_verified`, `legal_rights[].conflict`, `legal_rights[].sources`;
- `approvals[].document_id`, `approvals[].document_version_number`, `approvals[].binding_digest`;
- `source_documents`, `excluded_documents`.

`approvals` lists current approvals only. `extraction_metadata.passport_version` is the number of approval records ever created for the asset; it never decreases.

Stage 9B-4 added further fields, all additive:
- per field: `evidence_status`, `verification_method`, `document_id`, `document_version_number`, `value_origin`, `reviewer_attestation_status`;
- per source: `evidence_status`, `value_origin`, `reviewer_attestation_status`;
- per source document: `source_verified_claim_count`, `unknown_claim_count`;
- top level: `verification_summary`.

The passport also re-hashes each stored PDF on every read. See `EVIDENCE_VERIFICATION.md` §6.

## 5. Historical snapshots

`GET /extractions/{id}/approvals` returns every approval ever given, including stale and superseded ones. Each entry carries its complete binding (document version and SHA-256, evidence snapshot, asset snapshot, digests) and its evaluated `currency`. It requires `audit:read` and the issuer scope: a reviewer of another issuer gets 404, and institutional API keys get 403. Institutional clients never see history, only the current passport.

## 6. Database

Stage 9B-3 adds `assets.material_revision` and ten nullable binding columns to `review_approvals`. An existing database must be migrated with `scripts/migrate_9b3_approval_integrity.py`; see `MIGRATION_9B3_PLAN.md`. Until then, the application refuses to start on it (fail-closed) and changes nothing. Stage 9B-4 needs no schema change for approvals (format 2 is stored in the existing `evidence_snapshot` column). It adds three columns for correction page history, migrated with `scripts/migrate_9b4_correction_evidence.py` ([MIGRATION_9B4_CORRECTION_EVIDENCE.md](MIGRATION_9B4_CORRECTION_EVIDENCE.md)).

## 7. Not in this stage

- *Done in Stage 9B-4:* checking that a supporting passage really occurs on its source page.
- Approval policy: requiring `under_review` before approval, one approval per reviewer, a configurable quorum, and reject or revoke endpoints (Stage 9B-5).
