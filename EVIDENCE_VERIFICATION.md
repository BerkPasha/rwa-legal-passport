# Legal Evidence Verification (Stage 9B-4)

**Status:** reference documentation for the behaviour implemented in Stage 9B-4, steps 1–10 (2026-10-16). The work is in the uncommitted working tree on top of commit `07ad72f`. The source of truth is the code and the tests named in each section; where this document and the code disagree, the code is right and this document must be corrected.

Related documents:
- [APPROVAL_INTEGRITY.md](APPROVAL_INTEGRITY.md): approval binding and currency (Stage 9B-3, updated for 9B-4).
- [ACCESS_CONTROL.md](ACCESS_CONTROL.md): roles, permissions, tenant scope and the route inventory.
- [MIGRATION_9B4_CORRECTION_EVIDENCE.md](MIGRATION_9B4_CORRECTION_EVIDENCE.md): the step 7 database migration.
- [PRODUCT_READINESS.md](PRODUCT_READINESS.md) §12: Stage 9B-4 status, security review and acceptance.

---

## 0. Four separate concepts

The system keeps four things apart. None of them implies the next.

| Concept | What it is | Where it appears | What it does **not** establish |
|---|---|---|---|
| **A. AI output** | Values that an AI model extracted from the text of one document version | `legal_extractions`, `legal_claims`; `GET /extractions/{id}` | Correctness, completeness, legal meaning |
| **B. Quotation check** | A deterministic check that a quotation appears on its cited page of one document version | `evidence` on every claim; `evidence_status` in approvals and the passport | That the quotation supports the value; any legal fact |
| **C. Human review and approval** | A reviewer's correction or approval, recorded as an internal attestation | `review_corrections`, `review_approvals` | A legal opinion, certification or regulatory approval |
| **D. Legal validity** | Enforceability, ownership, entitlement, compliance | Nowhere: never established by this system | — |

Every AI extraction stores this disclaimer (§8): *"AI-GENERATED DRAFT — … A quotation check confirms only that the quoted text appears on the cited page of that document version; it does not confirm that the extracted value is correct. Reviewer corrections and approvals are internal attestations, not legal opinions. None of these establishes legal validity, ownership or enforceability. …"*

---

## 1. Legal claims and source evidence

### 1.1 Claims

An extraction (`POST /documents/{document_id}/extractions`) always uses the document's **current** version. It stores seven claims, one per legal field (`ownership_nature`, `income_rights`, `voting_rights`, `redemption_rights`, `transfer_restrictions`, `jurisdiction`, `insolvency_provisions`), each with `value`, `is_unknown`, `source_page` and `supporting_passage`. Extractions are always created as `draft`; nothing is approved automatically.

Before anything is stored, every AI result, real or mocked, passes `validate_ai_claims` (`app/services/ai.py`, step 3):
- A field that appears twice rejects the whole response (503). No claim is chosen, merged or overwritten, and nothing is stored.
- An unknown claim is stored canonically: value `"unknown"`, no page, no quotation. Missing fields are completed as unknown.
- JSON is parsed strictly: repeated keys and NaN are rejected, and pages and flags are not coerced from other types. A truncated (`max_tokens`) or text-less model response is invalid output (503).

Tests: `tests/test_ai_output_integrity.py`.

### 1.2 The quotation check

`app/evidence.py` (step 1) is a pure function of the quotation, the cited page number and the stored page texts of **one** document version. It uses no database, file, network or AI access.

- **Normalization (version 1), applied to the page and the quotation alike:**
  - Unicode NFC;
  - a fixed table of typographic equivalents (quotation marks, dashes, ligatures, ellipsis);
  - removal of invisible formatting characters;
  - runs of whitespace collapsed to one space.
  - Case, punctuation and digits are preserved. There is no case folding, no NFKC and no fuzzy matching.
- **Matching:**
  - exact substring of the normalized cited page, starting and ending on token boundaries (`"legal title"` never matches inside `"illegal title"`);
  - pages are never combined, so a quotation across a page break never matches.
- **Minimum size:** at least 20 normalized characters and 3 words.
- **No OCR:** a page without a text layer can never be verified.

| `evidence.status` | Meaning |
|---|---|
| `verified` | The quotation appears on the cited page. **The only positive status** |
| `not_found` | It appears on no page of this version |
| `found_on_other_page` | It appears, but not on the cited page (`found_on_pages` lists where) |
| `page_missing` | No page is cited |
| `page_out_of_range` | The cited page is not a page of this version |
| `no_text_layer` | The cited page has no extractable text |
| `quotation_missing` / `quotation_too_short` | No quotation / below the minimum size |
| `document_version_mismatch` | Text of another version was supplied (internal safety check) |
| `source_text_unavailable` / `source_text_invalid` | The version's page records are missing / inconsistent (duplicate or non-contiguous page numbers) |
| `not_applicable` | The claim is unknown; no evidence is required |

### 1.3 Where the check runs

The check is re-computed from stored data at every point where it matters. Nothing caches a positive result.

| Where | Against which text | Effect |
|---|---|---|
| `GET /extractions/{id}`, `GET /documents/{id}/extractions` | The extraction's **own** version (`extraction.version_id`), never a newer one | `claims[].evidence` (step 2) |
| `PATCH /extractions/{id}/claims/{claim_id}` | The extraction's own version, under the write lock | A correction is refused unless its effective quotation verifies (step 7) |
| `POST /extractions/{id}/approvals` | The extraction's version, under the write lock, after the stored PDF was checked (§4.3) | An approval is refused unless every known claim verifies (step 4) |
| Approval currency on every read | The approved version's live page text | `evidence_not_verified`, `source_text_changed` (step 5) |
| `GET /passport/...` | Each approved version's page text | `verified` and `evidence_status` per field and source (step 6) |

`claims[].evidence` (`ClaimEvidenceResponse`):

```json
{ "status": "verified", "cited_page": 2, "found_on_pages": [2], "page_count": 2, "version_number": 1,
  "verification_method": "exact_substring_token_boundary", "normalization_version": 1,
  "scope": "source_text_presence_only", "explanation": "…" }
```

`version_number` is null when the version's text is unavailable. Tests: `tests/test_evidence_verifier.py`, `tests/test_extraction_evidence.py`.

---

## 2. Reviewer corrections and evidence replacement

`PATCH /extractions/{extraction_id}/claims/{claim_id}`. Guard: `claim:correct` (issuer reviewers and independent legal reviewers in scope). Rules: `app/correction_rules.py`, applied under the database write lock to the claim **as stored at that moment**.

Request (`ClaimCorrectionRequest`; unknown keys are rejected, page and flag are strict types):

| Field | Required | Meaning |
|---|---|---|
| `corrected_value` | yes (1–2000 characters) | The corrected value; ignored and stored as `"unknown"` when `is_unknown` is true |
| `is_unknown` | yes (boolean) | The claim states nothing |
| `corrected_source_page` | no (integer ≥ 1, or null) | **Omitted:** keep the current page (unchanged value only). **null:** clear it |
| `corrected_passage` | no (1–2000 characters, or null) | **Omitted:** keep the current quotation (unchanged value only). **null:** clear it |
| `correction_note` | no (≤ 1000 characters) | Reviewer's note |
| `expected_claim` | **yes** (object; null is invalid) | Mandatory optimistic concurrency precondition: `{value, is_unknown, source_page, supporting_passage}` exactly as read. All four keys are required (null inside means "none"), with strict types and no other keys. See §2.4 |

### 2.1 Which evidence is preserved and which must be replaced

| Situation | Page and quotation | Result |
|---|---|---|
| Known claim, value and flag unchanged, fields omitted | **Preserved** (inherited from the stored claim) | Accepted if the inherited quotation verifies |
| Known claim, value unchanged, new page and/or quotation sent | Replaced by what was sent; an omitted field is kept | Accepted if the resulting quotation verifies |
| Known claim, **value changed** | **Must be replaced.** Both must be sent; the previous evidence is never reused for a new value | Without both: 422 `evidence_required` |
| Known claim, a field sent as `null` | Cleared | The quotation then cannot verify: 422 `evidence_not_verified` (e.g. `page_missing`). To remove evidence, mark the claim unknown |
| Sending the old quotation again with a new value | Allowed as an explicit act; recorded as such | Accepted if it verifies. Verification proves presence of the text, not that it supports the new value |

### 2.2 Known → unknown and unknown → known

| Transition | Rule |
|---|---|
| Known → unknown (`is_unknown: true`) | The value becomes `"unknown"` and page and quotation are cleared. Sending a non-null page or quotation is refused (422 `unknown_with_evidence`); omitting them or sending `null` is accepted |
| Unknown → known | Treated as a value change: page and quotation must both be sent and must verify |
| Known claim whose value states `"unknown"` (or is blank) | Refused: 422 `unknown_value_for_known_claim` |

### 2.3 Responses

| Status | When |
|---|---|
| 200 | Accepted. The response is the new audit record (§9) |
| 401 / 403 | Invalid credential / missing credential or role without `claim:correct` |
| 404 | Extraction or claim not found or outside the caller's scope (same body) |
| 409 | The extraction is approved (`"An approved extraction cannot be corrected."`); start a re-review first |
| 409 | `expected_claim` no longer matches the stored claim: `{"detail": "...", "correction_conflict": "claim_changed", "changed_fields": [...]}`. Nothing is changed (§2.4) |
| 422 | `expected_claim` missing, null, incomplete or malformed (validation list). Nothing is changed |
| 422 | Request validation (`detail` is a list), or a refused correction: `{"detail": "...", "correction_rule": "...", "evidence_status": "..." or null}`. A refused correction changes nothing |
| 503 | Database busy; retry |

A correction moves a `draft` extraction to `under_review`. Corrections are possible on an extraction of a superseded version, but such an extraction can never be approved (§5).

### 2.4 Concurrent corrections (optimistic concurrency, Stage 9B-4 issue A)

**The race.** A correction that omits page or quotation keeps the evidence stored **when it is processed**. If another reviewer saved different evidence after this reviewer read the claim, the omitted fields would inherit evidence this reviewer never saw. The write lock serializes the two corrections; it does not prevent this.

**The safeguard.** Every correction **must** carry `expected_claim`, the claim exactly as its reviewer read it:
- **Missing or malformed:** a request without it, with null, or with an incomplete or wrongly typed state is invalid (422, FastAPI validation list with `loc: ["body", "expected_claim", …]`). It never reaches the claim.
- **Compared under the lock:** under the same write lock, before any rule of §2.1, all four components (value, unknown flag, cited page, quotation) are compared with the stored claim.
- **Any difference:** refuses the correction with 409 (`correction_conflict: claim_changed`, `changed_fields` naming the components). Nothing is changed: no claim, correction record, approval or audit entry. There is no fallback to the current state.
- **Every kind of correction:** the precondition covers keep, replace, value change and unknown, so it also prevents silently overwriting another reviewer's correction.
- **Scope first:** another tenant gets the same 404 as for a missing extraction. Without a precondition it gets the same 422 as any malformed request.

**Coverage.** Since the precondition became mandatory (2026-10-16, an authorized breaking change), **every** accepted correction is bound to the state its client stated, compared atomically under the write lock. No request can inherit evidence saved after its client read the claim.
- **Legacy clients:** a client that does not send `expected_claim` is rejected with 422 instead of inheriting.
- **The review interface:** it sends the claim as it was **shown when the correction form was opened**; a reload of the page never replaces it. On a 409 it shows a conflict notice, reloads the claim and never retries automatically; the reviewer must reopen the form on the current claim.
- **What the server cannot know:** whether a client actually showed the state it sends. A client that fetches the claim just before sending and copies it into `expected_claim` without showing it defeats the purpose. The review interface does not do this, and a frontend test guards it.

Tests:
- §2.1–2.3: `tests/test_correction_evidence.py` (69 tests), `tests/test_review.py`;
- §2.4: `tests/test_correction_concurrency.py` (36 tests: legacy and null rejection, stale refusal, every component and kind of correction, invalid preconditions, approval binding, cross-tenant, OpenAPI, parallel reviewers), `frontend/src/test/CorrectionConflict.test.tsx` (8 tests).

---

## 3. Document version binding

### 3.1 How versions are identified

| Identifier | Properties |
|---|---|
| `document_versions.id` (`version_id`) | UUID, never reused or changed |
| `version_number` | 1, 2, 3 … per document. Allocated atomically; unique per document (index `uq_document_versions_doc_version`) |
| `sha256_hash` | SHA-256 of the stored PDF bytes, recorded at upload and re-checked on download, at approval and on every passport read |
| Page records | `document_pages` rows of exactly one `version_id`, written once at upload; no API changes or deletes them |

An upload never changes an existing version. An extraction stores the `version_id` it was made from, and every later check of its claims uses that version's pages. A newer upload therefore never changes what an older extraction is checked against, and never changes the text returned for an older version (step 8).

### 3.2 What an approval is bound to (evidence snapshot format 2)

New approvals bind their evidence in **format 2** (`evidence_record`, hashed into `evidence_digest` and the `binding_digest`):

```json
{ "format": 2,
  "method": { "scope": "source_text_presence_only", "verification_method": "exact_substring_token_boundary",
              "normalization_version": 1, "min_quotation_chars": 20, "min_quotation_words": 3, "hash_algorithm": "sha256", … },
  "source": { "document_version_id": "…", "version_number": 1, "document_sha256": "…", "page_count": 2,
              "source_text_sha256": "…" },
  "claims": [ { "claim_id": "…", "field_name": "…", "value": "…", "is_unknown": false, "source_page": 2,
                "supporting_passage": "…", "correction_id": "…" or null, "document_version_id": "…",
                "evidence_status": "verified", "page_text_sha256": "…", "quotation_sha256": "…" } ] }
```

Format 1 (Stage 9B-3, a plain claim list) is still read. A format-1 approval stays current only while its claims verify live, because format 1 recorded no verification.

### 3.3 When an approval becomes stale

An approval record is never modified. Its currency is evaluated on every read. It is current only if **none** of these reasons applies (all failing reasons are reported):

| Reason | Cause |
|---|---|
| `unbound_legacy_approval` | Created before Stage 9B-3; nothing was bound |
| `binding_integrity_failed` | A stored snapshot or the binding no longer matches its digest, or a format-2 record contradicts the binding columns |
| `re_review_in_progress` | The extraction was reopened (`POST /extractions/{id}/review`) |
| `document_not_active` | The document is not active |
| `document_version_superseded` | A newer version of the document exists |
| `document_hash_mismatch` | The approved version's recorded SHA-256 differs from the binding |
| `evidence_changed` | A claim differs from the approved claim (value, flag, page, quotation, or the correction behind it) |
| `asset_information_changed` | Material asset information changed after approval |
| `asset_not_active` | The asset is suspended or retired |
| `source_text_changed` | **9B-4:** the stored page text of the approved version no longer matches the approved hash |
| `evidence_not_verified` | **9B-4:** a known claim's quotation no longer verifies on its page |
| `source_file_integrity_failed` | **9B-4, passport only:** the stored PDF is missing or no longer the approved bytes |

States per extraction (`approval_state.state`): `not_approved`, `current`, `stale` (re-review possible) and `superseded` (newer version; only a new extraction can be approved).

Tests: `tests/test_evidence_binding.py`, `tests/test_approval_integrity.py`.

---

## 4. Version-specific source text

### 4.1 `GET /documents/{document_id}/versions/{version_number}/text` (step 8)

Read-only. It reads only the database (no file access) and has no Stellar dependency. Guard: `document:read`, with the document's issuer in scope.

| Parameter | Where | Rule |
|---|---|---|
| `document_id` | path | Must exist and be in the caller's scope |
| `version_number` | path, required | Integer 1 … 2147483647. **No fallback** to the current or any other version |
| `version_id` | query, optional (1–64 characters) | When given (for example an extraction's `version_id`), it must identify that same version |

Response 200 (`DocumentVersionTextResponse`):
- `document_id`, `document_status`, `version_id`, `version_number`, `version_created_at`, `sha256_hash`;
- `is_current_version`, `current_version_number`, `page_count`;
- `source_text_sha256`: the same value that a format-2 approval of this version binds as `source.source_text_sha256`;
- `text_origin` (`pdf_text_layer_extracted_at_upload`), `notice`;
- `pages[]`: `{page_number, text_content}` for pages 1 … n. A page without a text layer is an empty string; text is never invented.

| Status | Body | When |
|---|---|---|
| 404 | `{"detail": "Document not found."}` | The document does not exist **or** is outside the caller's scope (identical; existence is not revealed) |
| 404 | `{"detail": "Document version not found."}` | In scope, but no such version |
| 409 | `{"detail": "Version reference mismatch: …"}` | `version_id` does not identify that version; no text is served |
| 409 | `{"detail": "…", "source_text_unavailable_reason": "no_page_records" \| "invalid_page_records"}` | The stored page records are missing or inconsistent. Logged; nothing is substituted |
| 422 | validation list | Invalid version number or empty `version_id` |
| 401 / 403 | | Invalid / missing credential; institutional keys get 403 |

### 4.2 `GET /documents/{document_id}/text`

Returns the pages of whatever version is **current** at the time of the request, with `document_id`, `version_number`, an additive `version_id` and `pages`. Use the version-specific endpoint to read the version an extraction or approval belongs to.

### 4.3 How the stored text is tied to the stored file

- **At approval (step 5):** the stored PDF is read and hash-checked before the lock, re-extracted with the upload method, and compared with the page records under the lock. Any mismatch refuses the approval with 409 `source_integrity_failure`:
  - `version_unavailable`, `file_unavailable`, `file_hash_mismatch`, `file_unreadable`;
  - `version_changed`, `page_count_mismatch`, `page_numbering_mismatch`, `page_text_mismatch`.
- **On every passport read:** the stored PDF is re-hashed (step 6).
- **Not on the text endpoints:** they serve the stored page records without re-reading the file.

Tests: `tests/test_version_text.py` (34 tests); source integrity in `tests/test_evidence_binding.py` and `tests/test_approval_evidence_gate.py`.

---

## 5. Review and approval workflow

| Step | Endpoint | Guard | Rules |
|---|---|---|---|
| Start review | `POST /extractions/{id}/review` | `review:start` | `draft` → `under_review`. On an `approved` extraction whose approvals are all stale it starts the **re-review**. 409 if an approval is current or the version is superseded |
| Correct | `PATCH /extractions/{id}/claims/{claim_id}` | `claim:correct` | §2 |
| Approve | `POST /extractions/{id}/approvals` (`{"notes": "…"}` optional) | `approval:create` | Checks run in this order under the write lock (see below) |
| History | `GET /extractions/{id}/corrections`, `GET /extractions/{id}/approvals` | `audit:read` | Every record, with evaluated currency (§9) |

Approval checks, in order:
1. A user id starting with `ai-` gets 403.
2. The extraction's version must be the current version: 409.
3. The document must be active: 409.
4. The asset must be active: 409.
5. An approved extraction whose approvals are not current needs a re-review first: 409.
6. The stored file must match its records (§4.3): 409 `source_integrity_failure`.
7. Every known claim must have verified evidence: 422, `evidence_failures: [{claim_id, field_name, status}]`.
8. Success: 201, an approval with binding format 2, `evidence_record` and `currency`.

The approval type is derived from the role: an issuer reviewer gives `issuer_attestation`, an independent legal reviewer gives `legal_verification`. A request cannot choose it.

**What an approval means:** the reviewer attests, internally, that the extraction was reviewed. Every approval stores this disclaimer: *"INTERNAL REVIEW ONLY — This approval reflects a reviewer's internal assessment and does NOT constitute legal certification, guarantee of legal validity, regulatory compliance confirmation, or a binding legal opinion under any jurisdiction. …"* The machine value `legal_verification` names the reviewer type; it does not mean legal validity was verified. The interface labels it **Independent Legal Review**.

**Not implemented (Stage 9B-5):** an `under_review` requirement before the first approval, one approval per reviewer, a quorum, and reject or revoke. A single issuer attestation is enough for a passport to be served, labelled as issuer attested.

Tests: `tests/test_approval_evidence_gate.py` (59 tests), `tests/test_review.py`, `tests/test_approval_integrity.py`.

---

## 6. Passport generation and evidence presentation

`GET /passport/{network}/{asset_code}/{issuer_key}`. Guard: an institutional API key with `passport:read` whose scope covers the asset; 60 requests per minute per client.

| Situation | Response |
|---|---|
| Asset unknown or outside the key's scope | 404 `Asset not found.` |
| Asset suspended or retired | 409 |
| Approvals exist but none is current (including every document excluded by file integrity) | 409 `The legal approval for this asset is no longer current; a re-review is required.` |
| Never approved | 404 |
| At least one current approval | 200, built from current approvals only |

How the passport is built:
- Each document with a current approval contributes its most recently approved extraction.
- If documents state different values for a field (compared ignoring whitespace and case), the field becomes a conflict: `value: "conflicting"`, all sources listed, no value chosen.
- Documents whose approval is not current are listed in `excluded_documents` with the reasons.

Per field (`legal_rights[]`) and per source (`sources[]`):

| Field | Meaning |
|---|---|
| `evidence_status` | The quotation check, re-computed on this read (§1.2) |
| `verified` | Known, not conflicting, and `verified` in **every** source. Source-text presence only |
| `source_page`, `supporting_passage`, `document_id`, `document_version_number` | Populated only when `verified` is true |
| `independently_verified` | `verified` **and** every source has a current `legal_verification` approval. An internal attestation on top of a found quotation, not a legal opinion; an issuer attestation never sets it |
| `value_origin` | `ai_extraction` or `reviewer_correction` (from the bound correction history) |
| `reviewer_attestation_status` | `independent_reviewer_attested`, `issuer_attested` or `not_applicable` |

Top level:
- **`verification_level`:** `issuer_attested`, `partially_independently_verified` or `independently_verified`. It is derived from the verified fields only. **`issuer_attested` is also the value when no field is verified at all**, so read it together with `verification_summary` (§11, issue B).
- **`verification_summary`:**
  - counts: `source_verified_field_count`, `independently_attested_field_count`, `unverified_field_count`, `unknown_field_count`, `conflicting_field_count`;
  - attestation flags: `reviewer_attestation_recorded`, `independent_reviewer_attestation_recorded`;
  - `legal_interpretation_status` (always `not_independently_established`);
  - `meaning`, a plain-language statement of what the indicators do and do not establish.
- **`disclaimer`:** *"INSTITUTIONAL USE ONLY — … does NOT constitute legal advice, … or a binding legal opinion …"*

Reviewer identities are never returned. The interface shows "No source-verified fields" instead of the level when the count is 0.

Tests: `tests/test_passport_verification.py` (34 tests), `tests/test_passport.py`.

---

## 7. Review interface (frontend, step 9)

- **Evidence badges:** they come only from `evidence.status`, for example "Quote found on page 2" or "Quote not found in document". The counters (Quote Found / Not Verified / Unknown) count backend statuses, never the presence of a passage.
- **Source text panel:** it shows the extraction's own version, located by its exact `version_id` in the version list and requested with both number and id.
  - Another version is shown only when selected explicitly, with a notice.
  - A slow or mismatched response is never shown.
  - If the version is not in the list, no text is shown and nothing is substituted.
- **Correction form:** page and quotation each offer "keep" or "replace"; "keep" is disabled when the value changed or the claim was unknown. New evidence fields start empty and are never pre-filled.
  - Every correction sends the claim as shown when the form was opened as `expected_claim` (mandatory).
  - A 409 `claim_changed` closes the form, names what changed, reloads the claim and is never retried automatically.
  - Marking a claim unknown sends explicit `null`s.
  - Before sending, the form re-reads the claim and refuses to send if it changed.
  - Backend refusals are shown with the rule and status.
- **Approval state:** "approved" is shown only when the backend `approval_state.state` is `current`.
- **Language:** English only. The interface shows the disclaimers it receives, unchanged.

Tests: `frontend/src/test/ReviewSourceText.test.tsx`, `CorrectionForm.test.tsx`, `correctionRequest.test.ts`, `PassportEvidence.test.tsx`, `EnglishDisclaimers.test.tsx`.

---

## 8. AI-generated information and disclaimers

| Text | Stored | Wording |
|---|---|---|
| AI extraction disclaimer (`UNVERIFIED_DISCLAIMER`) | With every new extraction (`legal_extractions.disclaimer`) | "AI-GENERATED DRAFT — …" (full text in `app/services/ai.py`) |
| Approval disclaimer (`APPROVAL_DISCLAIMER`) | With every approval | "INTERNAL REVIEW ONLY — …" |
| Passport disclaimer | Every passport response | "INSTITUTIONAL USE ONLY — …" |
| Blockchain disclaimer | Every blockchain-status response | "BLOCKCHAIN VERIFICATION ONLY — …" |
| Source text notice | Every version-text response | "… evidence material only: it is not a legal interpretation and does not establish legal validity …" |

- **Stored records keep the text they were created with.** Extractions created before step 10 may carry the earlier Turkish disclaimer; they are historical data and are never rewritten. The development database holds no extraction.
- **Disclaimer wording is not part of the approval binding,** so changing it never makes an approval stale.
- **Model input (step 10):**
  - pages are sent as `=== PAGE n ===` blocks; an empty page is `(empty page)` and a document without text is `(document contains no extractable text)`;
  - the page text itself is passed unchanged, never translated;
  - the prompt requires each quotation to be copied character for character from a single page, without ellipses, with `page_number` from that page's marker.
- **Not verified:** every test uses a mocked or in-process AI client. The behaviour of the real model, including the effect of the step 10 prompt rule, has not been measured. That requires a paid API call and approval (planned as 9B-6).

Tests: `tests/test_ai_disclaimer.py`, `tests/test_language_policy.py`.

---

## 9. Audit records and historical evidence

| Record | Content | Mutability |
|---|---|---|
| `review_corrections` | Reviewer, role, value / unknown flag / quotation before and after, **source page before and after** (step 7), `evidence_audit_version`, note, time | Never modified or deleted by the application |
| `review_approvals` | Approval type, reviewer, notes, disclaimer, binding (§3.2) | Never modified; currency is evaluated on read |
| `legal_extractions.disclaimer` | The disclaimer at creation time | Never rewritten |
| `document_versions`, `document_pages` | File hash and page texts of each version | Written once at upload |

- `GET /extractions/{id}/corrections` returns `original_source_page`, `corrected_source_page` and `page_history_recorded`. The last is false for corrections recorded before the step 7 migration, whose pages were never stored.
- `GET /extractions/{id}/approvals` returns every approval, including stale and superseded ones, with its snapshot, `evidence_snapshot_format`, `evidence_record` and `currency`.
- Original document text and quotations are stored and returned exactly as uploaded or entered, in any language; nothing is translated.

---

## 10. Tenant isolation and authorization

See [ACCESS_CONTROL.md](ACCESS_CONTROL.md) for the full matrix.
- **Issuer scope:** every document, version, extraction, claim, correction and approval inherits its issuer through the asset. A resource outside the caller's issuers answers exactly like a missing one (404, same body). On the version-text endpoint, a foreign document answers 404 for every version number, so the existence of versions is not revealed.
- **Separate credentials:** reviewers use bearer tokens. Institutional API keys only read passports (403 elsewhere).
- **Safe errors:** a `version_id` of another document or tenant is refused with the same 409 as an unknown id, without text. Responses contain no internal file paths.

---

## 11. Integrity, concurrency and limitations

### What a found quotation establishes and what it does not

It establishes **only** that the normalized quotation is a substring, on token boundaries, of the stored text of the cited page of one specific document version.

It does **not** establish:
- that the quotation supports the extracted value or the reviewer's correction;
- the legal meaning of the text;
- that the document is authentic, complete, current or binding;
- that a right is enforceable, an owner entitled, or an issuer solvent or compliant.

An approval adds a reviewer's internal attestation, not a legal opinion. **Legal enforceability is never independently guaranteed** by this system: it would require qualified, independent legal counsel acting outside it.

### Concurrency

Correction, re-review and approval take the SQLite write lock (`lock_extraction_for_update`) before they read and check the state. Concurrent requests on the same database are therefore serialized, and every rule is evaluated on the live claim. A correction's `expected_claim` precondition is compared under the same lock (§2.4).

### Known limitations and open decisions

| ID | Limitation | Effect | Status |
|---|---|---|---|
| A | **Concurrent corrections.** A correction that omits page or quotation keeps the evidence stored when it is processed. The mandatory `expected_claim` precondition refuses it (409) whenever that evidence differs from what its client read (§2.4) | — | **Resolved** for every client: requests without the precondition are rejected (422). Residual: the server cannot verify that a client displayed the state it sends |
| **B** | **`verification_level` floor.** `issuer_attested` is also returned when no field is verified (e.g. a passport of unknown or conflicting fields only). The names `independently_verified` / `partially_independently_verified` describe independent **reviewer attestation**, not legal verification | API consumers that read only this field can overstate the passport | Open decision; `verification_summary` is the unambiguous source. Adding an enum value would be a contract change |
| C | Unkeyed SHA-256 digests | Someone with write access to both the database and storage can forge a consistent state that stays current | Accepted for now; HMAC or signatures need a key and a decision |
| D | SQLite single-writer lock | The serialization guarantees hold for SQLite only; another database needs row locks | Accepted (9C) |
| E | No OCR; physical page numbers | Scanned pages never verify; `source_page` is the physical page index, which can differ from printed page labels | Accepted |
| F | No unique index on `document_pages (version_id, page_number)` | Duplicates are detected and refused (409 / `source_text_invalid`), not prevented | Accepted; adding it is a schema change |
| G | Real AI model untested | The step 10 prompt rule and the model's quotation accuracy are unmeasured | Open (9B-6, paid call needs approval) |
| H | Approval policy | No quorum, one-per-reviewer rule, reject or revoke | Open (9B-5) |

---

## 12. Endpoint summary (as implemented)

| Method and route | Guard | Success | Main errors |
|---|---|---|---|
| `POST /documents/{id}/extractions` | `extraction:run`, per-user rate limit | 201 extraction (`draft`) | 404, 429, 503 (AI error or invalid AI output; nothing stored) |
| `GET /documents/{id}/extractions`, `GET /extractions/{id}` | `extraction:read` | 200 with `claims[].evidence`, `approval_state` | 404 |
| `PATCH /extractions/{id}/claims/{claim_id}` (`expected_claim` required) | `claim:correct` | 200 correction record | 404, 409 (approved, or `claim_changed`), 422 (missing or malformed `expected_claim`, or `correction_rule`), 503 |
| `POST /extractions/{id}/review` | `review:start` | 200 | 404, 409, 503 |
| `POST /extractions/{id}/approvals` | `approval:create` | 201 approval | 403, 404, 409 (incl. `source_integrity_failure`), 422 (`evidence_failures`), 503 |
| `GET /extractions/{id}/corrections`, `/approvals` | `audit:read` | 200 history | 404 |
| `GET /documents/{id}/versions` | `document:read` | 200 version list | 404 |
| `GET /documents/{id}/text` | `document:read` | 200 current version's pages | 404 |
| `GET /documents/{id}/versions/{n}/text` | `document:read` | 200 (§4.1) | 404, 409, 422 |
| `GET /documents/{id}/download` | `document:read` | 200 PDF (SHA-256 re-verified) | 404, 500 (integrity failure; not served) |
| `GET /passport/{network}/{asset_code}/{issuer_key}` | API key `passport:read` | 200 passport | 401, 403, 404, 409, 429 |

All routes return 401 for an invalid credential and 403 for a missing one or an insufficient role. The full route inventory, including asset and upload routes, is in [ACCESS_CONTROL.md](ACCESS_CONTROL.md) §3; the interactive schema is at `/docs`.

---

*This document describes engineering behaviour. It is not a legal opinion, security certification or compliance attestation.*
