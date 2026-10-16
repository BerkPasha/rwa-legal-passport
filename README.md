# RWA Legal Passport

Institutional legal transparency API for tokenized real-world assets (RWA).

---

## Requirements

- Python 3.11 or later
- Windows 10/11

---

## Installation (Windows)

### 1. Clone or open the project

```powershell
cd C:\Users\<your-username>\rwa-legal-passport
```

### 2. Create a virtual environment

```powershell
python -m venv .venv
```

### 3. Activate the virtual environment

```powershell
.venv\Scripts\activate
```

> Once it is active, you will see `(.venv)` in the terminal.

### 4. Install the dependencies

```powershell
pip install -r requirements.txt
```

### 5. Copy the environment variables

```powershell
copy .env.example .env
```

### 6. Set up access control (users, roles, issuer scopes)

Every endpoint except `/health` requires authentication. Users, their roles, issuer (tenant) scopes and institutional API keys are defined in the `access_control.json` file; the file contains **only SHA-256 digests** and is not added to Git.

```powershell
copy access_control.example.json access_control.json
python -m app.access_tool new-token      # for each user / key: hand over the token, write the digest into the file
python -m app.access_tool check          # validate the file
```

Tokens are entered in the **Credentials** panel in the UI sidebar (kept only for that browser tab). For roles, the permission matrix, the route inventory, CORS and migration from the old `.env` tokens, see **[ACCESS_CONTROL.md](ACCESS_CONTROL.md)**.

Approvals are bound to the exact document version, evidence and asset information, and outdated approvals are never presented as current. See **[APPROVAL_INTEGRITY.md](APPROVAL_INTEGRITY.md)**. Quotations are checked against the stored text of their cited page, reviewer corrections follow explicit evidence rules, and the source text of any document version can be read. See **[EVIDENCE_VERIFICATION.md](EVIDENCE_VERIFICATION.md)** for what these checks establish, and what they do not.

A database created before Stage 9B-3 must be migrated first (**[MIGRATION_9B3_PLAN.md](MIGRATION_9B3_PLAN.md)**), and then to Stage 9B-4 (**[MIGRATION_9B4_CORRECTION_EVIDENCE.md](MIGRATION_9B4_CORRECTION_EVIDENCE.md)**, which also gives the order for older databases). Until then the server refuses to start, without modifying any data.

> Stage 9B-1 added the `issuer_id` column to the `assets` table. With an old-schema database the server refuses to start without modifying any data; you can back up an empty development database, delete it and let it be recreated.

---

## Run the server

```powershell
uvicorn app.main:app --reload
```

The server starts at `http://127.0.0.1:8000`.

| URL | Description |
|-----|----------|
| `http://127.0.0.1:8000/health` | Health check |
| `http://127.0.0.1:8000/docs` | Swagger UI (interactive API documentation) |
| `http://127.0.0.1:8000/redoc` | ReDoc documentation |

---

## Run the frontend

```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173, talks to the API at http://localhost:8000
```

The interface is English-only. `npm run build` writes the production build to `frontend/dist`.

---

## Run the tests

```powershell
pytest -v                      # backend: uses in-memory or scratch databases, never rwa_legal_passport.db
cd frontend; npm test          # frontend (Vitest)
cd frontend; npx tsc --noEmit  # type check
```

No test makes a paid AI call or contacts the Stellar network: both are replaced by mocks.

---

## Contributing and language policy

All code, comments, documentation, API messages, logs, CLI text, tests and UI text are written in English. Multilingual test fixtures, original legal evidence, user data and historical reports are the only exceptions. See **[CONTRIBUTING.md](CONTRIBUTING.md)** for the full standard. `tests/test_language_policy.py` enforces it as part of `pytest`.

---

## Project structure

```
rwa-legal-passport/
├── app/
│   ├── main.py                 # FastAPI application, startup schema check
│   ├── config.py               # Settings (pydantic-settings, .env)
│   ├── access_registry.py      # Users, issuers, institutional keys (access_control.json)
│   ├── auth.py, api_keys.py, authorization.py, permissions.py   # Authentication, roles, tenant scope
│   ├── evidence.py             # Deterministic quotation check (no I/O)
│   ├── evidence_records.py     # Quotation check against a version's stored page text
│   ├── correction_rules.py     # Reviewer correction evidence rules
│   ├── approval_integrity.py   # Approval binding and currency
│   ├── source_integrity.py     # Stored PDF vs page records, checked at approval
│   ├── db/                     # Engine, session, schema check
│   ├── models/, schemas/       # Database models, API schemas
│   ├── routers/                # assets, documents, extractions, review, passport, blockchain, auth, health
│   └── services/               # AI (Anthropic), PDF, storage, Stellar Horizon
├── frontend/                   # React + Vite + TypeScript review interface (English-only)
├── scripts/                    # Database migrations (9B-3, 9B-4)
├── tests/                      # Backend tests (pytest)
├── .env.example                # Example environment variables (.env itself is never committed)
├── access_control.example.json # Access configuration template (digests only)
├── pytest.ini, requirements.txt
└── *.md                        # Documentation. PRODUCT_READINESS.md (9A audit plus stage status log)
                                #   and ENGLISH_MIGRATION_*.md are records: earlier sections are kept as written
```
