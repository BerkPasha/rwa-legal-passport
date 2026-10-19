# Stellar Testnet evidence anchoring

The Legal Passport can commit a cryptographic digest of its evidence to **Stellar Testnet**. Anyone can then check, against public network data, that the passport's evidence is exactly what was committed, and when.

An anchor proves only that the anchoring account recorded a digest at a ledger time. **It is not a legal approval.** It does not establish legal validity, ownership, regulatory compliance, the authenticity of documents or an independent legal opinion. Mainnet anchoring is not supported.

---

## 1. Asset identity

A passport belongs to a classic Stellar asset: network + asset code + issuer account (`assets.blockchain_network`, `stellar_asset_code`, `stellar_issuer_public_key`; immutable after registration).

- **Issuer key:** must be a valid StrKey `G…` public key (56 characters, base32, version byte and CRC16 checksum checked with the official `stellar-sdk`). Keys with a bad checksum are refused at registration (422).
- **Asset code:** 1–12 uppercase alphanumeric characters, not `XLM`; `credit_alphanum4` up to 4 characters, otherwise `credit_alphanum12`.
- A valid identifier does not prove that the asset exists or who controls it.
- Soroban: the manifest's `identifier_type` is `stellar_classic_asset`; a contract identity can be added later as another type without changing the anchoring.

## 2. Evidence manifest (what the digest commits to)

`app/evidence_manifest.py` builds the manifest from exactly what the passport currently serves (the same selection code). Schema `rwa-legal-passport/evidence-manifest`, version 1:

| Key | Content |
|---|---|
| `asset` | `identifier_type`, `network`, `network_passphrase`, `asset_code`, `asset_issuer`, `stellar_asset_type`, `asset_id`, `issuer_organization`, `rwa_asset_type`, `jurisdiction`, `status`, `material_revision` |
| `documents[]` (by `document_id`) | `document_id`, `document_type`, `extraction_id`, `evidence`, `approvals` |
| `documents[].evidence` | The format 2 evidence record: `method` (verification rules), `source` (`document_version_id`, `version_number`, `document_sha256`, `page_count`, `source_text_sha256`), `claims[]` (`claim_id`, `field_name`, `value`, `is_unknown`, `source_page`, `supporting_passage`, `correction_id`, `document_version_id`, `evidence_status`, `page_text_sha256`, `quotation_sha256`) |
| `documents[].approvals[]` | The counted current approvals: `approval_type`, `reviewer_role`, `approved_at`, `binding_digest` |
| `excluded_documents[]` | `document_id`, `reasons` |
| `verification` | `verification_level` (legacy), `verification_status`, `issuer_attestation_recorded`, `legal_reviewer_approval_recorded`, field counts |

**Digest:** SHA-256 of the canonical JSON (sorted keys, no insignificant whitespace, UTF-8), as lowercase hex.

- The digest changes whenever any committed element changes: a new document version, a correction, an approval, an exclusion or asset information. It does not depend on the time of reading.
- The manifest contains no reviewer identities, credentials, internal tenant ids or document files.
- It stays **off-chain**: it is stored with the anchor and returned to authorized clients. Only the 32-byte digest goes on-chain.
- Without a current approved passport there is no manifest, and nothing can be anchored (409 `no_current_passport`).

## 3. On-chain commitment

One classic transaction per manifest, signed by the configured Testnet anchoring account (`app/services/stellar_anchor.py`, official `stellar-sdk` 16.1.0):

| Part | Value |
|---|---|
| Network passphrase | `Test SDF Network ; September 2015` (fixed in code; the Horizon server's passphrase is checked before every submission and verification) |
| Source account | The anchoring account (`STELLAR_ANCHOR_SECRET_FILE`) |
| Memo | `MEMO_HASH` = the 32-byte manifest digest |
| Operation | One `manage_data`: name `lp:<asset id>`, value = the same 32 bytes (also the account's latest commitment for that asset) |
| Time bounds | Valid for `STELLAR_ANCHOR_TX_TIMEOUT_SECONDS` (default 300 s) after signing |
| Fee | `STELLAR_ANCHOR_BASE_FEE` stroops per operation at most (default 1000) |

No smart contract is deployed. Submission and reads go through Horizon (`https://horizon-testnet.stellar.org`). Stellar RPC keeps only a bounded recent history (about 7 days by default), while Horizon serves Testnet transactions since the last reset.

## 4. Submission, idempotency and failures

`POST /assets/{asset_id}/stellar-anchors` (permission `anchor:create`: platform operator and issuer reviewers, issuer-scoped).

1. Testnet assets only (409 `mainnet_not_supported`), with a valid identity (422 `invalid_asset_identity`) and a current passport (409 `no_current_passport`).
2. **Idempotent:**
   - A digest that is already confirmed or pending for the asset returns the existing anchor (200) and submits nothing.
   - A pending anchor is re-checked first.
   - A failed attempt is retried on the same record (`attempts`).
3. The transaction hash is computed before submission, and the record is committed **before** the transaction is sent. A lost response therefore never leads to a second transaction.
4. Submission outcome:

   | Horizon answer | Anchor status | HTTP |
   |---|---|---|
   | 200 | `confirmed` (with the ledger) | 201 |
   | 400 | `failed` (`failure_reason` = result codes, e.g. `tx_insufficient_fee`) | 502 |
   | Timeout or network error | `pending` | 202 |

5. A pending anchor is resolved by `POST …/{anchor_id}/refresh`:
   - the matching successful transaction found → `confirmed`;
   - an unsuccessful one → `failed`;
   - not found after its time bounds have expired → `failed` (`expired_not_included`).

   A network error changes nothing.
6. Refused before anything is stored (503): `anchoring_not_configured`, `anchor_account_not_found` (unfunded, or Testnet was reset), `wrong_network`, `network_unavailable`.
7. One in-process lock serializes signing (the account's sequence numbers). Across several processes, the unique constraint `(asset_id, network, manifest_digest)` still prevents a second record of the same digest.

## 5. Verification

- **Institutional:** `GET /passport/{network}/{asset_code}/{issuer_key}/stellar-anchor`, with an X-API-Key (`passport:read`) and the same scope as the passport.
- **Internal:** `GET /assets/{asset_id}/stellar-anchors/verification` (`asset:read`).

Verification is read-only:
1. It recomputes the current manifest digest from the live records.
2. It chooses the anchor of the current digest. Otherwise it takes the latest confirmed anchor, otherwise the latest one.
3. It checks the stored record: the stored manifest must hash to its digest and name this asset.
4. It checks the live network: the server is on Testnet, and the transaction is found, successful, from the **configured** anchoring account, with `memo_hash` = digest and exactly one `manage_data` `lp:<asset id>` = digest.

| `result` | Meaning |
|---|---|
| `verified` | All checks pass **and** the current manifest has the anchored digest |
| `evidence_changed` | A valid on-chain commitment of different (earlier) evidence, or no current passport to compare |
| `not_anchored` | No anchor exists |
| `pending` | Submitted, not (yet) found, time bounds not expired |
| `failed` | Failed, unsuccessful, or expired without inclusion |
| `onchain_mismatch` | The network transaction does not match the record or the trusted account |
| `transaction_unavailable` | A confirmed anchor the network no longer returns (e.g. after a Testnet reset) |
| `network_unavailable` | Horizon unreachable, too slow, answering with an unexpected status or a malformed response, or on another network (`onchain.error`) |
| `anchoring_not_configured` | No trusted anchoring account is configured |
| `anchor_record_invalid` | The stored record fails its own digest or asset check |

Only `verified` sets `verified: true`. The response includes `committed_manifest_canonical_json`; its SHA-256 is the anchored digest, so a client can check the commitment independently on any Horizon server.

### Horizon reads (Stage 10C-3B, T5)

**No cache, by design.** The API and the interface promise a *live* check: the endpoint descriptions, the "Live network checks" of the interface and `checked_at`. A cached confirmation would be shown as one, and could survive a Testnet reset or an on-chain change. So every verification reads the network again. Only the following is shared or saved:
- **One connection per verification.** The network passphrase (`GET /`), the transaction and its operations are read through one HTTP client. Before T5 there were two.
- **No operations read for an unsuccessful transaction.** Its outcome (`failed`) never depends on them.
- **Concurrent identical verifications share one read.** A verification of the same transaction, on the same Horizon server and network, that is still waiting for Horizon is joined instead of repeated.
  - The read exists only while Horizon answers it: a finished read is never reused, and no request receives a result that was complete before it asked.
  - Each request still recomputes its own manifest and runs its own record, account, memo, operation and digest checks. A request whose evidence changed meanwhile gets `evidence_changed`.
  - The read runs as its own task: a requester that disconnects does not cancel it for the others. A failed read fails every request that joined it (`network_unavailable`), and nothing negative is kept.
  - At most 64 reads are shared per process; beyond that, reads run on their own.
  - Internally the result is marked `shared`. The response is unchanged.

Measured with the fake Horizon:

| Scenario | Requests before → after T5 | Connections before → after |
|---|---|---|
| 1 verification | 3 → 3 | 2 → 1 |
| 10 sequential | 30 → 30 | 20 → 10 |
| 10 concurrent | 30 → 3 when all overlap | 20 → 1 |
| Unsuccessful transaction | 3 → 2 | 2 → 1 |

**Bounded, and never a false confirmation.**
- One verification read takes at most 30 seconds in total. Before T5, each of its three requests could take 30 seconds.
- An unexpected HTTP status (429, 5xx), a refused connection, a timeout, or a malformed answer is `network_unavailable`, never `failed` or `verified`. A malformed answer is non-JSON, the wrong JSON shape, a non-boolean `successful`, another transaction's `hash`, a malformed `ledger` or time, or malformed operations.
- A pending anchor whose refresh receives a malformed answer stays pending, so it is never declared failed and submitted again. Before T5, a missing `successful` field counted as an unsuccessful transaction.
- Nothing is retried automatically.

**Public Horizon dependency.**
- Each verification costs two or three requests to `horizon-testnet.stellar.org`, which rate-limits per client address. A busy public page can therefore reach that limit; the answer is then `network_unavailable`, never a stale confirmation.
- A Testnet reset makes every transaction disappear. The next verification reports `transaction_unavailable` at once.
- Submission, signing, and the network check before signing are unchanged by T5.

## 6. Setup and demo

1. **Create and fund the Testnet anchoring account** (once; again after a Testnet reset):
   ```
   python scripts/stellar_testnet_setup.py
   ```
   It writes `secrets/stellar_testnet_anchor.json` (`network`, `public_key`, `secret_seed`) only if the file is missing, funds the account through Friendbot, and prints the public key and balance. It never prints the seed. `secrets/` is gitignored.
2. **End-to-end demo** (disposable database, fictional test credentials, mock AI; real Testnet anchoring):
   ```
   python scripts/stellar_testnet_e2e.py
   ```
   It runs: register → document → claims → approvals → manifest → anchor → verify → independent Horizon check → change detection.
3. **Browser demo** (verification-only server; no transaction, no paid AI): [DEMO.md](DEMO.md).

Configuration (`.env.example`): `STELLAR_ANCHOR_SECRET_FILE`, `STELLAR_ANCHOR_BASE_FEE`, `STELLAR_ANCHOR_TX_TIMEOUT_SECONDS`.

**Verification-only mode:** an account file with only `network` and `public_key` (no `secret_seed`) makes the server trust that account for verification and refuse every anchoring request (503 `anchoring_not_configured`). Use it for public demos and read-only deployments, so the signing seed stays on a single controlled machine.

**Interface:**
- Asset page, tab *Stellar Evidence Anchor*:
  - the verification, the anchoring history and the explorer links;
  - re-check and pending refresh;
  - an "Anchor current evidence on Testnet" action, offered only when the current evidence is not anchored and confirmed by the user before anything is submitted.
- *Legal Passport* lookup: the same commitment, read with the institutional key.

A blockchain result is never shown as legal or independent verification.

## 7. Data model

Table `stellar_anchors` (`app/models/stellar_anchor.py`), with one row per committed manifest:
- the manifest, its schema, version and digest;
- the account, the data entry name, the transaction hash, the signed envelope (public data), `valid_until` and `status`;
- `attempts`, the ledger and its close time, `failure_reason`, `requested_by` and the timestamps.

The table is additive:
- On an up-to-date database it is created, empty, at application startup.
- An out-of-date database is refused before anything is created (the startup check now runs before table creation).
- Existing rows are never changed.
- Rows of this table are never deleted.

## 8. Limitations

- **Testnet only and resettable:** after a reset, the transaction and the account disappear. Verification then reports `transaction_unavailable`, and the account must be funded again.
- **Trust in the anchoring account:** whoever holds its secret can anchor any digest. Verification trusts only the configured account; the secret must be protected like any credential.
- **The anchor attests time and content, not truth.** A digest of wrong or legally invalid evidence is still a valid commitment.
- **History source:** verification depends on Horizon's retained history. A third party can verify on any Horizon server or public explorer.
- **Single process:** sequence numbers are serialized per process; several processes anchoring at once can see `tx_bad_seq`, and the failed attempt can then be retried.

- **Live dependency:** verification needs the public Horizon at every request (no cache, see §5). An outage or a rate limit makes it `network_unavailable` for its duration.
- **A confirmed transaction is not legal validity.** It is not ownership or regulatory approval either; see the disclaimer of every verification response.

Tests: `tests/test_stellar_anchoring.py` (fake Horizon via `tests/fake_horizon.py`; real signing and XDR); `tests/test_horizon_verification.py` (Horizon reads, T5).
