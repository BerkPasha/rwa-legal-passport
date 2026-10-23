# DoraHacks submission text: BLI Legal Tech Hackathon 2

Copy-paste text for the BUIDL page. Every statement describes the existing, working product. Fields marked `[…]` are decided by the team on DoraHacks.

---

## Project name

RWA Legal Passport

## Tagline

The legal evidence layer for tokenized real-world assets.

## Short description (under 280 characters)

One reviewable legal record per tokenized asset: versioned documents, claims quoting exact pages, human approvals that stop applying when a document changes, and an evidence digest anchored and verified on Stellar Testnet.

## Summary (under 1,200 characters)

A tokenized asset is issued once, but its legal documents keep changing. RWA Legal Passport gives every asset one reviewable legal record.

Each PDF version is kept with its SHA-256 hash. Each legal claim cites a page and an exact quotation, which the server checks against that page's stored text. The issuer and an independent legal reviewer approve, and each approval is bound to the exact version, claims and asset information it covered. When a new version arrives, the old approvals stop applying automatically, and the history is kept.

Institutions read the current approved claims, the approvals and the full history through a scoped, read-only Legal Passport API. The passport's evidence manifest is hashed and anchored on Stellar Testnet (memo hash + manage_data), and it is verified against Horizon.

An anchor proves integrity and time, never legal validity. Try the read-only demo at https://legalpassport.vercel.app/demo. All data is synthetic.

---

## Full description

### The problem

Tokenization puts real-world assets on-chain, but their legal side still lives in PDFs and data rooms. Prospectuses are amended and terms are revised. Nobody can tell which version a summary came from, who approved it, or whether that approval still holds after the document changed. For issuers, custodians and institutional investors, this makes legal due diligence slow, manual and hard to audit.

### What we built

RWA Legal Passport is a legal evidence workflow and API for tokenized assets. It has five stages:

1. **Asset.** A Stellar asset (asset code + issuer key) is registered under its issuer.
2. **Legal documents.** PDFs are uploaded as versions. Each version keeps its SHA-256 hash and per-page text, and a new version supersedes the old one without deleting it.
3. **Legal claims and evidence.** Each claim cites a page and an exact quotation. The server verifies the quotation against the stored text of that page of that version. Unverified claims cannot be approved.
4. **Human approvals.** An issuer attestation and an independent legal review are each bound to the version, claims and asset information they covered, through a SHA-256 binding digest. Approvals are never edited. They stop applying when anything they covered changes, for example a new document version.
5. **Cryptographic verification.** The passport's evidence manifest is canonical JSON, and its SHA-256 digest is committed in one Stellar Testnet transaction (memo hash + `manage_data`). Verification checks the transaction on Horizon, the anchoring account, the memo and the operation. It also checks that the current evidence still has exactly that digest.

Institutions read the result through the **Legal Passport API**, with a scoped, read-only key: the currently approved claims, who attested them, every earlier version and approval, and the Stellar evidence.

### Stellar integration

- Real Stellar Testnet transactions, signed by a dedicated anchoring account through the official `stellar-sdk`. Submission and verification go through Horizon.
- The demo asset LPDEMO was anchored twice:
  - version 1: transaction `1687c0dd…628123d2`, ledger 5241718;
  - after its prospectus changed, version 2: transaction `843a5a6d…3c1240da`, ledger 5285093.
- Each memo hash equals that version's evidence manifest digest. The current verification matches version 2. The version 1 anchor is kept as history and no longer matches the current evidence.
- Mainnet anchoring is refused by design.

### Try it

- Website: https://legalpassport.vercel.app/
- Interactive demo (read-only, no sign-in, synthetic data): https://legalpassport.vercel.app/demo
- Current anchor on Stellar Testnet: https://stellar.expert/explorer/testnet/tx/843a5a6d9101b3e93a591c88008cf81eca9f25d0f2bd803d5741ef6c3c1240da
- Source code: https://github.com/BerkPasha/rwa-legal-passport
- Demo video: `[link]`

### Tech stack

- Python, FastAPI, SQLAlchemy (async) and SQLite;
- `pypdf` for PDF text, `stellar-sdk` and Horizon for Stellar Testnet;
- React, TypeScript, Vite and Tailwind CSS;
- pytest, Vitest and Playwright;
- the public website and demo are hosted on Vercel.

### Honest scope

- All data is synthetic.
- Approvals are internal attestations, not legal opinions.
- A Stellar anchor proves that an exact evidence digest was recorded at a ledger time and is unchanged since; it does not prove legal validity, ownership or compliance.
- The public site hosts the read-only demo. It shows a bundled snapshot of the LPDEMO record, with a recorded verification, plus two illustrative records. The FastAPI backend and the full review application run locally.
- Optional AI-assisted claim drafting (Anthropic) is disabled by default and has not been verified live.
- There is no OCR, and Stellar Testnet only.

---

## Other fields

| Field | Value |
|---|---|
| Tags | RWA, LegalTech, RegTech, Compliance, Tokenization, Stellar |
| Track / bounty | `[choose on DoraHacks]` |
| Team | `[team members on DoraHacks]` |
| Logo | `frontend/src/assets/brand/rwa-legal-passport-logo.jpg` |
