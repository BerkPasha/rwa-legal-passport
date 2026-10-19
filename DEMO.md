# RWA Legal Passport — Stellar Testnet demo

A local, reproducible demonstration of the MVP with **synthetic, non-confidential data** and a **real Stellar Testnet transaction**. Design details: [STELLAR_ANCHORING.md](STELLAR_ANCHORING.md).

## What is real and what is local

| Part | Real or local |
|---|---|
| Evidence commitment | **Real** Stellar Testnet transaction `1687c0dd…123d2` (ledger 5241718), memo hash and `manage_data` = the manifest digest |
| Verification | **Real**: every verification reads Horizon Testnet live and recomputes the manifest locally |
| Asset, document, claims, approvals | Local, in a disposable SQLite dataset under `scratch/stellar-demo/demo` (gitignored), with synthetic data |
| Legal-rights extraction | Mock AI output from the test suite; no paid AI call |
| Users and API key | The **fictional** credentials of the test suite; they are public and authenticate only the demo dataset |
| Asset issuer key | A random, well-formed Testnet key. The LPDEMO token itself was not issued on-chain; linking an identifier proves no ownership |

The demo server is **verification-only**. Its anchoring file holds a public key and no secret seed, so it verifies the existing commitment but cannot sign or submit a transaction. No paid AI call can be made either.

## 1. Prepare the dataset (once)

The dataset is derived from the live run of `scripts/stellar_testnet_e2e.py`. Its copy is in `scratch/stellar-demo/original-run`.

```powershell
.venv\Scripts\python scripts/prepare_stellar_demo.py --source scratch/stellar-demo/original-run
```

- The script copies the run into `scratch/stellar-demo/demo`.
- In that copy only, it reverts the run's two final change-detection steps (an added document and a tampered claim).
- It refuses to finish unless the recomputed manifest digest equals the anchored digest `5c760dba…d057`.
- It writes the demo access registry, the verification-only anchoring file and `demo.json`, which lists the identifiers and the fictional credentials.

(Already done in this workspace.)

## 2. Run the demo

Stop the normal development servers first, or use other ports. They also use ports 8000 and 5173.

```powershell
# Terminal 1 — backend on the demo dataset (verification-only, no paid AI; .env is not changed)
.venv\Scripts\python scripts/run_stellar_demo.py

# Terminal 2 — frontend
cd frontend
npm run dev
```

Open http://localhost:5173. In the sidebar **Credentials** panel, enter:
- **User token:** `test-issuer-token` (issuer reviewer). Alternatively, `test-legal-token` (legal reviewer) or `test-admin-token` (operator).
- **Institutional API key:** `test-inst-key-alpha`.

If the backend runs on another port, start the frontend with `$env:VITE_API_URL="http://127.0.0.1:<port>"; npm run dev`.

## 3. Walk-through

1. **Asset:** *Assets* → **LPDEMO** (Stellar Testnet, issuer `GB5M55…RDCC`).
2. **Supporting document:** *Documents & Extractions* → "Prospectus" v1 (with its SHA-256).
3. **Legal rights claims:** open the extraction (*Review →*) to see the seven fields:
   - four known claims, each with a cited page, a quotation and evidence status **verified**;
   - three unknown claims.
4. **Source evidence:** the source text panel shows the exact page text of the approved version.
5. **Approval status:** "Approval current", with an issuer attestation and an independent legal reviewer approval. Both are internal attestations.
6. **Evidence manifest:** *Stellar Evidence Anchor* tab → *View the committed evidence manifest* (canonical JSON; its SHA-256 is the anchored digest).
7. **Stellar Testnet anchoring record:**
   - transaction hash, ledger and close time;
   - links to Stellar Expert and Horizon;
   - the anchoring account and the data entry `lp:<asset id>`.
8. **Verification:** the result is **"Commitment verified on Stellar Testnet"**:
   - every live network check is ✓;
   - "Current evidence matches the recorded commitment";
   - *Re-check on Stellar* queries the network again.
9. **Complete Legal Passport:** *Open Legal Passport →* (prefilled) → *Fetch Legal Passport*. This shows:
   - rights, evidence and approvals;
   - the verification level and status;
   - the same **Stellar Evidence Commitment**, read with the institutional API key.

Independent check, outside the application:
- https://stellar.expert/explorer/testnet/tx/1687c0ddc4a85b07b3ec13e9064aa57a4556e8993b9ff78080b5ac9d628123d2
- https://horizon-testnet.stellar.org/transactions/1687c0ddc4a85b07b3ec13e9064aa57a4556e8993b9ff78080b5ac9d628123d2

In both, the memo (base64) decodes to the digest shown in the application.

**History for the API consumer** (versions, every approval and the anchoring records; stored states, no network call). In the interface: the asset page → **View Passport History →** (it uses the institutional API key from the Credentials panel). Through the API:

```powershell
curl.exe -H "X-API-Key: test-inst-key-alpha" http://127.0.0.1:8000/passport/stellar-testnet/LPDEMO/GB5M55ZEKFTRJIU76EFLE3BMMHMUP2PWHFD77NCXOOO6GUDFEIF2RDCC/history
```

After a new document version is uploaded (*Documents & Extractions* → **Upload New Version** on the document, as the issuer reviewer or operator; or `POST /documents/{id}/versions`), the passport answers 409 and the history shows version 1's approvals as `superseded` (`document_version_superseded`) and the anchor with `matches_current_evidence: false`, until version 2 is reviewed and approved. Do this on a copy of the dataset (`--demo <copy>`): it changes the demo database.

**Change detection** is shown by the original live run (`scratch/stellar-demo/original-run/e2e_report.json`): after a new document and after a tampered claim, the result was `evidence_changed` and nothing was verified.

## 3b. Live re-anchoring after a new version (real Testnet transaction, needs approval)

`run_stellar_demo.py` is verification-only and refuses anchoring. To anchor version 2's evidence, run the backend on a **copy** of the dataset with the signing account file `secrets/stellar_testnet_anchor.json` (Testnet only; the same account anchored version 1):

```powershell
Copy-Item -Recurse scratch\stellar-demo\demo scratch\stellar-demo\live-copy
$env:APP_ENV = "development-demo"; $env:AI_PROVIDER = "disabled"; $env:ANTHROPIC_API_KEY = ""
$env:DATABASE_URL = "sqlite+aiosqlite:///scratch/stellar-demo/live-copy/demo.db"
$env:STORAGE_PATH = "scratch\stellar-demo\live-copy\storage"
$env:ACCESS_CONTROL_FILE = "scratch\stellar-demo\live-copy\access_control.json"
$env:STELLAR_ANCHOR_SECRET_FILE = "secrets\stellar_testnet_anchor.json"
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

1. As `test-issuer-token`: **Upload New Version** (for example `tests/fixtures/real_pdfs/trust_prospectus.pdf`), then **Add Manual Claim** with a quotation copied from the new version's page text, and approve it.
2. As `test-legal-token`: add the independent approval. The passport is current again; its history shows version 1 as superseded.
3. As `test-issuer-token`: *Stellar Evidence Anchor* → the result is "Evidence changed since anchoring" (version 1's anchor) → **Anchor current evidence on Testnet** → confirm. **This submits one real Testnet transaction.**
4. The card then shows the new transaction (status, hash, explorer links, ledger) and "Current evidence matches the recorded commitment"; in *Anchoring History* only the new record says "Matches the current evidence".

If Testnet was reset since the account was funded, run `python scripts/stellar_testnet_setup.py` first (it funds the account through Friendbot; also a network action that needs approval).

## 4. What the demo does not claim

- A blockchain commitment records **when** evidence existed and that it has **not changed** since. It is not legal validity, legal approval, ownership or independent verification. The passport's own verification indicators stay exactly as they are.
- Stellar Testnet is reset periodically (next scheduled reset: 16 December 2026). Afterwards the transaction is gone (`transaction_unavailable`), and a new anchor must be created with `scripts/stellar_testnet_setup.py` and `scripts/stellar_testnet_e2e.py`. That submits a real Testnet transaction, so it needs approval.
- Mainnet anchoring is disabled.
