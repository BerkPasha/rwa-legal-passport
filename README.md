# RWA Legal Passport

**The legal evidence layer for tokenized real-world assets.** RWA Legal Passport gives every tokenized asset one reviewable legal record. The record holds its documents and every version of them, the legal claims drawn from those documents with the exact page they quote, and the human approvals bound to the version they reviewed. It also tracks what has changed since. An integrity commitment of that record is anchored on **Stellar Testnet**.

| | |
|---|---|
| Website | https://legalpassport.vercel.app/ |
| Interactive demo (read-only, synthetic data, no sign-in) | https://legalpassport.vercel.app/demo |
| Demo record anchored on Stellar Testnet (current, version 2) | [Transaction `843a5a6d…3c1240da`](https://stellar.expert/explorer/testnet/tx/843a5a6d9101b3e93a591c88008cf81eca9f25d0f2bd803d5741ef6c3c1240da) · ledger 5285093 |
| Earlier anchor of the same asset (version 1, superseded) | [Transaction `1687c0dd…628123d2`](https://stellar.expert/explorer/testnet/tx/1687c0ddc4a85b07b3ec13e9064aa57a4556e8993b9ff78080b5ac9d628123d2) · ledger 5241718 |
| Architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |

> All data in the demo and in this repository is synthetic and fictional. An approval in this product is an internal attestation, not a legal opinion. A Stellar anchor proves that an exact evidence digest was recorded at a ledger time and that the record is unchanged since. It does not prove legal validity, ownership or compliance.

---

## The problem

A token is issued once, but the legal documents behind it keep changing: prospectuses are amended, opinions are replaced, terms are revised. Today the legal side of a tokenized asset lives in data rooms and PDFs. Nobody can tell which document version a summary came from, who approved it, or whether that approval still applies after an amendment.

## What the product does

1. **Asset registry.** An asset is registered by its Stellar asset code and issuer key, under its issuer (tenant). The issuer key's checksum is validated with the official `stellar-sdk`.
2. **Versioned legal documents.** PDFs are uploaded as versions of a document. Each version keeps its SHA-256 hash and its per-page text, and a new version supersedes the previous one without deleting it.
3. **Legal claims with page-level evidence.** Each claim cites a page and an exact quotation. The server checks the quotation against the stored text of that page of that version. A claim whose quotation cannot be found cannot be approved.
4. **Human approvals bound to evidence.** An issuer reviewer attests and an independent legal reviewer verifies. Each approval stores a binding: document version, file hash, claims, quotation-check results and asset information, digested with SHA-256. Approvals are never edited or deleted. Whether an approval still applies is decided on every read, so a new document version, a changed claim or changed asset information makes it stop applying until the record is reviewed again.
5. **Legal Passport API.** Institutions read the currently approved claims, the approvals behind them, the full version history and the Stellar evidence through a scoped, read-only API key (`GET /passport/{network}/{asset_code}/{issuer_key}`, plus `/history` and `/stellar-anchor`).
6. **Stellar Testnet anchoring and verification.** The passport's evidence manifest (canonical JSON) is hashed with SHA-256. That digest is committed in one Testnet transaction, as the `MEMO_HASH` and a `manage_data` entry. Verification re-reads the transaction from Horizon and checks the network, the source account, the memo and the operation. It then checks that the stored manifest hashes to the anchored digest and that the current evidence still has exactly that digest.

The [interactive demo](https://legalpassport.vercel.app/demo) walks through all of this on one asset (LPDEMO). Version 1 of its prospectus was approved and anchored. Version 2 then superseded it, which invalidated the version 1 approvals. Version 2 was reviewed, approved and anchored again. The two transactions above are the real Testnet commitments of that record.

## Architecture in brief

- **Backend:** Python, FastAPI and SQLAlchemy (async) on SQLite, with PDF files in local storage and text extraction with `pypdf`. Stellar Testnet is reached through Horizon with the official `stellar-sdk`.
- **Frontend:** React, TypeScript, Vite and Tailwind. One codebase builds two targets:
  - the full review application, which talks to the API;
  - the public website with the read-only demo (`npm run build:public`), which carries no backend calls and no credentials.
- **Access control:** Bearer tokens for reviewers and `X-API-Key` keys for institutions. Both are stored only as SHA-256 digests, with roles and per-issuer tenant isolation. The passport API and AI extraction are rate-limited, and public deployment profiles add per-client and failed-authentication limits.

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Run it locally

Requirements: Python 3.11+ and Node.js 20+. The commands below are for Windows PowerShell; on macOS or Linux use `source .venv/bin/activate` and `cp`.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
copy access_control.example.json access_control.json
python -m app.access_tool new-token     # once per user or key: hand out the token, put its digest in access_control.json
python -m app.access_tool check
uvicorn app.main:app --reload           # API on http://127.0.0.1:8000 (/docs for the OpenAPI UI)

cd frontend
npm install
npm run dev                             # review application on http://localhost:5173
```

Tokens are entered in the application's **Credentials** panel. Roles and scopes are described in [ACCESS_CONTROL.md](ACCESS_CONTROL.md). Anchoring on Stellar Testnet needs a funded Testnet account (`python scripts/stellar_testnet_setup.py`), kept in `secrets/`, which is never committed; see [STELLAR_ANCHORING.md](STELLAR_ANCHORING.md).

**Tests:**

```powershell
pytest                          # backend
cd frontend; npm test           # frontend (Vitest)
cd frontend; npx tsc --noEmit   # type check
```

Real-browser end-to-end tests (Playwright) are described in [E2E_TESTING.md](E2E_TESTING.md). No test makes a paid AI call or a Stellar transaction.

## Known limitations

- **Only the website and the read-only demo are hosted.** The demo shows a bundled snapshot: the LPDEMO record as the API returned it, plus two illustrative synthetic records (LPFUND, LPNOTE) with no Stellar transaction. Its anchor check is the one recorded on 19 Oct 2026 22:31 UTC, not a live check. The FastAPI backend and the full review application currently run locally only.
- **Stellar Testnet only.** Mainnet anchoring is refused by design. Testnet is reset periodically, after which Testnet anchors can no longer be looked up.
- **AI extraction is optional and disabled by default.** It can draft claims with Anthropic's API. The live model integration has not been verified, and every AI draft still requires human review and verified quotations.
- **No OCR.** Pages without a text layer (scanned images) cannot be quoted.
- **Single-node storage.** SQLite and local file storage suit one server process; there is no managed backup or multi-instance setup.
- **Tokens are issued manually** in `access_control.json`. They do not expire, and there is no self-service sign-up.

## Documentation

| Topic | Document |
|---|---|
| Architecture overview | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Approval binding and currency | [APPROVAL_INTEGRITY.md](APPROVAL_INTEGRITY.md) |
| Quotation checks and evidence rules | [EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md) |
| Stellar Testnet anchoring and verification | [STELLAR_ANCHORING.md](STELLAR_ANCHORING.md) |
| Users, roles, tenants and API keys | [ACCESS_CONTROL.md](ACCESS_CONTROL.md) |
| Optional AI extraction | [AI_EXTRACTION.md](AI_EXTRACTION.md) |
| The demo dataset | [DEMO.md](DEMO.md) |
| Deployment profiles and checks | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Browser end-to-end tests | [E2E_TESTING.md](E2E_TESTING.md) |
| Demo video script | [docs/DEMO_VIDEO_SCRIPT.md](docs/DEMO_VIDEO_SCRIPT.md) |

Other Markdown files in the repository root are dated development records, kept as written.

## Repository safety

The repository holds no credentials, databases or private documents:
- `.env`, `access_control.json`, `*.db`, `secrets/`, `backups/` and `scratch/` are excluded by `.gitignore`;
- the PDFs in `tests/fixtures/` are fictional test documents;
- the keys that appear in tests are fictional placeholders that are never valid.

Code, documentation and UI are English-only ([CONTRIBUTING.md](CONTRIBUTING.md)).
