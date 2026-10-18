"""
Prepare the local Stellar demo dataset from a completed stellar_testnet_e2e.py run (DEMO.md).

    python scripts/prepare_stellar_demo.py --source <e2e workdir> [--out scratch/stellar-demo/demo]

- Copies the run's disposable database and document storage to --out. The source is never
  modified; an existing --out is refused.
- The e2e run ends with two change-detection steps: a new document and a tampered claim. In
  the COPY they are reverted to the anchored state: documents absent from the anchored manifest
  are removed, and claims are restored to their anchored values. The script then recomputes the
  manifest and refuses to finish unless its digest equals the anchored digest.
- Writes a demo access registry (the FICTIONAL test-suite credentials, as SHA-256 digests) and a
  verification-only anchoring file (public key only, no secret seed). The demo server can verify
  the real Testnet transaction but can never sign one.

No network call is made and no transaction is submitted. The development database is never used.
"""

import argparse
import asyncio
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.evidence_manifest import build_manifest, manifest_digest  # noqa: E402
from app.models.asset import Asset  # noqa: E402
from app.services.storage import StorageService  # noqa: E402
from tests.conftest import (  # noqa: E402  (fictional credentials of the test suite)
    ADMIN_TOKEN, INST_KEY_ALPHA, ISSUER_TOKEN, LEGAL_TOKEN, SECOND_LEGAL_TOKEN, build_test_access_config,
)

CLAIM_FIELDS = ("value", "is_unknown", "source_page", "supporting_passage")


def _restore_anchored_state(con: sqlite3.Connection, anchor: sqlite3.Row) -> list[str]:
    manifest = json.loads(anchor["manifest_json"])
    kept = {d["document_id"] for d in manifest["documents"]}
    notes = []
    for (doc_id,) in con.execute("SELECT id FROM documents WHERE asset_id = ?", (anchor["asset_id"],)).fetchall():
        if doc_id in kept:
            continue
        versions = [r[0] for r in con.execute("SELECT id FROM document_versions WHERE document_id = ?", (doc_id,))]
        extractions = [r[0] for r in con.execute("SELECT id FROM legal_extractions WHERE document_id = ?", (doc_id,))]
        for ex in extractions:
            for table in ("review_approvals", "review_corrections", "legal_claims"):
                con.execute(f"DELETE FROM {table} WHERE extraction_id = ?", (ex,))
            con.execute("DELETE FROM legal_extractions WHERE id = ?", (ex,))
        for version in versions:
            con.execute("DELETE FROM document_pages WHERE version_id = ?", (version,))
        con.execute("DELETE FROM document_versions WHERE document_id = ?", (doc_id,))
        con.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        notes.append(f"removed post-anchor document {doc_id}")
    for document in manifest["documents"]:
        for claim in document["evidence"]["claims"]:
            row = con.execute("SELECT value, is_unknown, source_page, supporting_passage FROM legal_claims WHERE id = ?",
                              (claim["claim_id"],)).fetchone()
            anchored = (claim["value"], int(claim["is_unknown"]), claim["source_page"], claim["supporting_passage"])
            if row is not None and tuple(row) != anchored:
                con.execute("UPDATE legal_claims SET value = ?, is_unknown = ?, source_page = ?, supporting_passage = ? "
                            "WHERE id = ?", (*anchored, claim["claim_id"]))
                notes.append(f"restored claim {claim['field_name']} to its anchored value")
    con.commit()
    return notes


async def _current_digest(db: Path, storage: Path, asset_id: str) -> str:
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            asset = (await session.execute(select(Asset).where(Asset.id == asset_id))).scalar_one()
            return manifest_digest(await build_manifest(session, asset, StorageService(str(storage))))
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the local Stellar demo dataset")
    parser.add_argument("--source", required=True, help="workdir of a stellar_testnet_e2e.py run")
    parser.add_argument("--out", default=str(ROOT / "scratch" / "stellar-demo" / "demo"))
    args = parser.parse_args()
    source, out = Path(args.source), Path(args.out)
    if out.exists():
        print(f"Refusing: {out} already exists.")
        return 2
    out.mkdir(parents=True)
    with sqlite3.connect(source / "demo.db") as src, sqlite3.connect(out / "demo.db") as dst:
        src.backup(dst)  # consistent copy; the source is only read
    shutil.copytree(source / "storage", out / "storage")

    con = sqlite3.connect(out / "demo.db")
    con.row_factory = sqlite3.Row
    anchor = con.execute("SELECT * FROM stellar_anchors WHERE status = 'confirmed' "
                         "ORDER BY created_at DESC LIMIT 1").fetchone()
    if anchor is None:
        print("Refusing: the source run has no confirmed anchor.")
        return 2
    notes = _restore_anchored_state(con, anchor)
    asset = con.execute("SELECT * FROM assets WHERE id = ?", (anchor["asset_id"],)).fetchone()
    con.close()

    digest = asyncio.run(_current_digest(out / "demo.db", out / "storage", anchor["asset_id"]))
    if digest != anchor["manifest_digest"]:
        print(f"Refusing: the restored manifest digest {digest} differs from the anchored {anchor['manifest_digest']}.")
        shutil.rmtree(out)
        return 1

    (out / "access_control.json").write_text(json.dumps(build_test_access_config(), indent=2), encoding="utf-8")
    (out / "anchor_public.json").write_text(json.dumps(
        {"network": "stellar-testnet", "public_key": anchor["anchor_account"]}, indent=2), encoding="utf-8")
    info = {
        "asset": {"id": asset["id"], "network": asset["blockchain_network"], "asset_code": asset["stellar_asset_code"],
                  "issuer_key": asset["stellar_issuer_public_key"]},
        "anchor": {"manifest_digest": anchor["manifest_digest"], "transaction_hash": anchor["transaction_hash"],
                   "ledger": anchor["ledger"], "anchor_account": anchor["anchor_account"],
                   "horizon": f"https://horizon-testnet.stellar.org/transactions/{anchor['transaction_hash']}",
                   "explorer": f"https://stellar.expert/explorer/testnet/tx/{anchor['transaction_hash']}"},
        "fictional_demo_credentials": {
            "platform_admin_token": ADMIN_TOKEN, "issuer_reviewer_token": ISSUER_TOKEN,
            "legal_reviewer_token": LEGAL_TOKEN, "second_legal_reviewer_token": SECOND_LEGAL_TOKEN,
            "institutional_api_key": INST_KEY_ALPHA,
        },
        "restoration": notes,
    }
    (out / "demo.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(json.dumps(info, indent=2))
    print(f"\nDemo dataset ready: {out}\nThe manifest digest equals the anchored digest {digest}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
