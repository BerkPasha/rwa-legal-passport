"""
End-to-end Legal Passport → Stellar TESTNET anchoring demo (STELLAR_ANCHORING.md).

    python scripts/stellar_testnet_e2e.py [--anchor-file secrets/stellar_testnet_anchor.json]

Runs the whole MVP flow in-process against a DISPOSABLE SQLite database in a temporary directory:
the fictional test access registry and the mock AI (no paid AI call) are used; only the evidence
anchoring talks to the real Stellar Testnet (Horizon), with the account created by
scripts/stellar_testnet_setup.py. The development database is never touched.

Steps: register a Stellar-linked asset → upload a legal document → AI-extracted claims with
verified quotations → issuer and legal reviewer approvals → evidence manifest → anchoring
transaction on Testnet → verification through the Legal Passport API (live network) →
independent check of the transaction on Horizon → evidence change detection.

The secret seed is never printed. Testnet is reset periodically; the transaction stays
verifiable until the next reset.
"""

import argparse
import asyncio
import base64
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402
from stellar_sdk import Keypair  # noqa: E402

from app.access_registry import get_access_registry  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.rate_limit import InMemoryRateLimiter, get_extraction_rate_limiter, get_rate_limiter  # noqa: E402
from app.services.ai import get_ai_service  # noqa: E402
from app.services.stellar_anchor import StellarAnchorService, get_anchor_service  # noqa: E402
from app.services.storage import StorageService, get_storage  # noqa: E402
from tests.conftest import (  # noqa: E402  (fictional credentials of the test suite)
    ADMIN_TOKEN, INST_KEY_ALPHA, ISSUER_TOKEN, LEGAL_TOKEN, MERIDIAN_ISSUER, TEST_REGISTRY, bearer,
)
from tests.mock_ai import MockAIService  # noqa: E402
from tests.pdf_fixtures import make_source_pdf  # noqa: E402

HORIZON = "https://horizon-testnet.stellar.org"


def step(number: int, title: str) -> None:
    print(f"\n[{number}] {title}")


async def run(anchor_file: str) -> dict:
    workdir = Path(tempfile.mkdtemp(prefix="lp-stellar-e2e-"))
    engine = create_async_engine(f"sqlite+aiosqlite:///{(workdir / 'demo.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db_session():
        async with sessions() as session:
            yield session

    storage = StorageService(str(workdir / "storage"))
    anchoring = StellarAnchorService(anchor_file, base_fee=1000, tx_timeout_seconds=300)  # the REAL Testnet
    app.dependency_overrides.update({
        get_db: db_session,
        get_storage: lambda: storage,
        get_ai_service: lambda: MockAIService(),
        get_access_registry: lambda: TEST_REGISTRY,
        get_rate_limiter: lambda: InMemoryRateLimiter(max_requests=1000, window_seconds=60),
        get_extraction_rate_limiter: lambda: InMemoryRateLimiter(max_requests=1000, window_seconds=3600),
        get_anchor_service: lambda: anchoring,
    })
    admin, issuer, legal, inst = bearer(ADMIN_TOKEN), bearer(ISSUER_TOKEN), bearer(LEGAL_TOKEN), {"X-API-Key": INST_KEY_ALPHA}
    report: dict = {"workdir": str(workdir)}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e", timeout=120) as c:
            step(1, "Register a Stellar Testnet-linked RWA asset")
            issuer_key = Keypair.random().public_key  # an identifier only: linking proves no ownership
            asset = (await c.post("/assets", headers=admin, json={
                "issuer_id": MERIDIAN_ISSUER, "blockchain_network": "stellar-testnet", "stellar_asset_code": "LPDEMO",
                "stellar_issuer_public_key": issuer_key, "issuer_organization_name": "Meridian Property Holdings LLC",
                "asset_type": "real_estate", "jurisdiction": "US-NY",
            })).json()
            print(f"    asset {asset['id']}  LPDEMO:{issuer_key}")

            step(2, "Associate a legal document")
            doc = (await c.post(f"/assets/{asset['id']}/documents", headers=issuer,
                                data={"document_type": "prospectus", "title": "Prospectus"},
                                files={"file": ("prospectus.pdf", make_source_pdf(), "application/pdf")})).json()
            print(f"    document {doc['id']} v{doc['current_version']['version_number']} "
                  f"sha256={doc['current_version']['sha256_hash'][:16]}…")

            step(3, "Legal rights claims with supporting evidence, reviewed and approved")
            extraction = (await c.post(f"/documents/{doc['id']}/extractions", headers=issuer)).json()
            for headers in (issuer, legal):
                r = await c.post(f"/extractions/{extraction['id']}/approvals", headers=headers, json={})
                assert r.status_code == 201, r.text
            passport = (await c.get(f"/passport/stellar-testnet/LPDEMO/{issuer_key}", headers=inst)).json()
            print(f"    {len(extraction['claims'])} claims; passport verification_status="
                  f"{passport['verification_status']}, level={passport['verification_level']}")

            step(4, "Compute the evidence manifest and 5. submit its commitment to Stellar Testnet")
            r = await c.post(f"/assets/{asset['id']}/stellar-anchors", headers=issuer)
            anchor = r.json()
            print(f"    HTTP {r.status_code}  status={anchor.get('status')}  digest={anchor.get('manifest_digest')}")
            if r.status_code not in (201, 202):
                print(f"    anchoring did not succeed: {anchor}")
                report["anchor"] = anchor
                return report

            step(6, "Real transaction identifier")
            if anchor["status"] == "pending":
                for _ in range(10):
                    await asyncio.sleep(3)
                    anchor = (await c.post(f"/assets/{asset['id']}/stellar-anchors/{anchor['id']}/refresh",
                                           headers=issuer)).json()
                    if anchor["status"] != "pending":
                        break
            print(f"    tx {anchor['transaction_hash']}  ledger={anchor['ledger']}  status={anchor['status']}")
            print(f"    {anchor['horizon_transaction_url']}")

            step(7, "Verify the commitment against network data, 8. through the Legal Passport API")
            verification = (await c.get(f"/passport/stellar-testnet/LPDEMO/{issuer_key}/stellar-anchor",
                                        headers=inst)).json()
            print(f"    result={verification['result']} verified={verification['verified']} onchain={verification['onchain']}")

            print("    independent check on Horizon (outside this application):")
            async with httpx.AsyncClient(timeout=30) as h:
                tx = (await h.get(f"{HORIZON}/transactions/{anchor['transaction_hash']}")).json()
                ops = (await h.get(f"{HORIZON}/transactions/{anchor['transaction_hash']}/operations")).json()
            memo_hex = base64.b64decode(tx["memo"]).hex() if tx.get("memo_type") == "hash" else None
            op = ops["_embedded"]["records"][0]
            independent = {
                "successful": tx["successful"], "source_account": tx["source_account"],
                "memo_hex_equals_digest": memo_hex == anchor["manifest_digest"],
                "manage_data": op["name"], "manage_data_equals_digest": base64.b64decode(op["value"]).hex() == anchor["manifest_digest"],
                "ledger": tx["ledger"], "closed_at": tx["created_at"],
            }
            print(f"    {independent}")

            step(9, "Detect changes to the committed evidence")
            other = (await c.post(f"/assets/{asset['id']}/documents", headers=issuer,
                                  data={"document_type": "legal_opinion", "title": "Legal opinion"},
                                  files={"file": ("opinion.pdf", make_source_pdf("Opinion."), "application/pdf")})).json()
            ex2 = (await c.post(f"/documents/{other['id']}/extractions", headers=issuer)).json()
            await c.post(f"/extractions/{ex2['id']}/approvals", headers=issuer, json={})
            changed = (await c.get(f"/passport/stellar-testnet/LPDEMO/{issuer_key}/stellar-anchor", headers=inst)).json()
            print(f"    after a new approved document: result={changed['result']} verified={changed['verified']}")
            async with sessions() as s:
                await s.execute(text("UPDATE legal_claims SET value = 'Tampered.' WHERE extraction_id = :e "
                                     "AND field_name = 'jurisdiction'"), {"e": extraction["id"]})
                await s.commit()
            tampered = (await c.get(f"/passport/stellar-testnet/LPDEMO/{issuer_key}/stellar-anchor", headers=inst)).json()
            print(f"    after tampering a claim in the database: result={tampered['result']} verified={tampered['verified']}")

            report.update({
                "asset": {"id": asset["id"], "code": "LPDEMO", "issuer": issuer_key},
                "anchor": {k: anchor[k] for k in ("manifest_digest", "transaction_hash", "status", "ledger",
                                                   "ledger_closed_at", "anchor_account", "data_name",
                                                   "horizon_transaction_url")},
                "verification": {k: verification[k] for k in ("result", "verified", "evidence_matches_anchor",
                                                               "current_manifest_digest", "onchain")},
                "independent_horizon_check": independent,
                "after_new_document": {"result": changed["result"], "verified": changed["verified"]},
                "after_tampering": {"result": tampered["result"], "verified": tampered["verified"]},
            })
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
    (workdir / "e2e_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nReport: {workdir / 'e2e_report.json'}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Legal Passport → Stellar Testnet end-to-end demo")
    parser.add_argument("--anchor-file", default=str(ROOT / "secrets" / "stellar_testnet_anchor.json"))
    report = asyncio.run(run(parser.parse_args().anchor_file))
    ok = report.get("verification", {}).get("verified") is True and report.get("after_tampering", {}).get("verified") is False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
