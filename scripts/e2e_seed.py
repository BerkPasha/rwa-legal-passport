"""
Seeds the synthetic dataset of the browser end-to-end tests (Stages 10B-1 to 10B-3, E2E_TESTING.md).

Run by scripts/e2e_run.py only, in a fresh interpreter whose environment points at a temporary
directory and whose working directory is that directory (so the repository's .env is never loaded):

    python scripts/e2e_seed.py <temporary directory> [--public-showcase]

--public-showcase (Stage 10C-2, scripts/build_showcase.py) seeds the data of a public showcase: no
synthetic anchor records (a "pending" record would state a submission that never happened), an
existing verification-only anchoring account file is kept (the genuine demo anchor's), and the
journey PDF is not written.

Everything goes through the real HTTP API of the application (in process), with the real access
registry, upload validation, evidence check and approval gates. Three substitutes, all confined to
the disposable temporary directory and named for what they are:
- While seeding, the AI service is a scripted, offline list of claims for the synthetic fixture,
  recorded with the model name SEED_MODEL, so that it is never presented as an AI result. The
  server that the browser tests use afterwards runs with AI_PROVIDER=disabled.
- Two Stellar anchor records are written directly to the database to exercise how the interface
  presents a pending and a tampered (invalid) commitment. They are marked requested_by=SYNTHETIC
  and were never submitted to any network; the anchoring account file is verification-only (a
  public key without a seed), so nothing can be signed either.
- Two stored PDF files are removed or altered after upload, to exercise unavailable documents.
The seed also creates an approved passport of manually entered claims (E2EMNL) through the manual
claim route (Stage 10B-3), for the read-only showcase (Stage 10C-1).

Writes <root>/access_control.json (random fictional credentials, digests only),
<root>/anchor_public.json (verification-only account), <root>/manual_journey.pdf (a new synthetic
text PDF that is never uploaded here: the browser uploads it and quotes it in the manual claim
journey, Stage 10B-3) and <root>/context.json (identifiers and those credentials, for the tests
only).
"""

import asyncio
import hashlib
import json
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "real_pdfs"
SEED_MODEL = "e2e-seed-scripted-claims (no AI model)"
SYNTHETIC = "e2e-seed-synthetic-record-never-submitted"
ISSUER_A, ISSUER_B = "e2e-meridian", "e2e-harbor"

# What the synthetic prospectus states: page and verbatim quotation per legal field, and a short
# synthetic value. Quotations are copied from the fixture's extracted text.
CLAIMS = {
    "ownership_nature": (1, "Unitholders do not acquire legal title to the underlying real estate; "
                            "legal title remains vested in the Trustee.",
                         "Fractional beneficial interest in the trust; legal title remains with the Trustee."),
    "income_rights": (2, "Net rental income shall be distributed quarterly, pro rata to the number of "
                         "Digital Units held",
                      "Quarterly distribution of net rental income, pro rata to units held."),
    "voting_rights": (2, "each Digital Unit carries one vote on matters reserved to unitholders",
                      "One vote per Digital Unit on matters reserved to unitholders."),
    "transfer_restrictions": (3, "approved by the Trustee — any other transfer is void",
                              "Transfers only to verified accredited investors approved by the Trustee."),
    "redemption_rights": (3, "The Trust does not offer a redemption facility",
                          "No redemption facility; exit only by transfer on an approved venue."),
    "insolvency_provisions": (4, "the Trust property shall not form part of the Trustee’s estate",
                              "Trust property is not part of the Trustee's estate on insolvency."),
    "jurisdiction": (4, "governed by the laws of England and Wales",
                     "Laws of England and Wales; exclusive jurisdiction of the courts of England."),
}
FABRICATED_QUOTE = "Unitholders may redeem their units monthly at net asset value."

# The manual claim journey's document (Stage 10B-3): synthetic, one statement per line
MANUAL_JOURNEY_PAGES = [
    ["HARBOURVIEW LOGISTICS NOTES - OFFERING MEMORANDUM (SYNTHETIC E2E FIXTURE)",
     "Each Digital Note is a senior unsecured obligation of Harbourview Logistics Ltd.",
     "Holders receive a fixed coupon of four percent per annum, paid semi-annually."],
    ["Notes may be transferred only to eligible investors registered with the Paying Agent.",
     "Holders may vote only on amendments that change the coupon or the maturity date."],
    ["This Memorandum and the Notes are governed by the laws of Ireland.",
     "Nothing in this synthetic memorandum creates a real obligation of any person."],
]
MANUAL_JOURNEY = {
    "field_name": "jurisdiction",
    "value": "Irish law governs the Memorandum and the Notes.",
    "page": 3,
    "quotation": "This Memorandum and the Notes are governed by the laws of Ireland",
    "fabricated_quotation": "The Notes are guaranteed by the Government of Ireland",
    "page_text": MANUAL_JOURNEY_PAGES[2][0],
}


def _fixture(name: str) -> bytes:
    listed = {parts[1]: parts[0] for parts in (line.split() for line in
              (FIXTURES / "SHA256SUMS").read_text(encoding="utf-8").splitlines()) if len(parts) == 2}
    data = (FIXTURES / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != listed.get(name):
        raise SystemExit(f"{name} is not the reviewed synthetic fixture (SHA-256 differs from SHA256SUMS)")
    return data


def _registry(root: Path) -> dict:
    """Writes the fictional access registry; returns the plaintext credentials (for the tests only)."""
    from app.access_registry import hash_token

    creds = {name: f"e2e-{name}-{secrets.token_urlsafe(18)}" for name in
             ("admin", "issuer_a", "legal_a", "issuer_b", "inst_a", "inst_b")}
    users = [
        ("e2e-operator", "E2E Operator", "platform_admin", [], "admin"),
        ("e2e-issuer-reviewer-a", "E2E Issuer Reviewer A", "issuer_reviewer", [ISSUER_A], "issuer_a"),
        ("e2e-legal-reviewer-a", "E2E Legal Reviewer A", "independent_legal_reviewer", [ISSUER_A], "legal_a"),
        ("e2e-issuer-reviewer-b", "E2E Issuer Reviewer B", "issuer_reviewer", [ISSUER_B], "issuer_b"),
    ]
    config = {
        "issuers": [{"id": ISSUER_A, "name": "E2E Meridian Harbour Issuer"},
                    {"id": ISSUER_B, "name": "E2E Harbor Bonds Issuer"}],
        "users": [{"user_id": uid, "display_name": name, "role": role, "token_sha256": hash_token(creds[key]),
                   **({"issuer_ids": issuers} if issuers else {})} for uid, name, role, issuers, key in users],
        "institutional_clients": [
            {"client_id": "e2e-institution-a", "organization": "E2E Institution A", "key_sha256": hash_token(creds["inst_a"]),
             "issuer_ids": [ISSUER_A], "operations": ["passport:read"]},
            {"client_id": "e2e-institution-b", "organization": "E2E Institution B", "key_sha256": hash_token(creds["inst_b"]),
             "issuer_ids": [ISSUER_B], "operations": ["passport:read"]},
        ],
    }
    (root / "access_control.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    return creds


class _SeedClaims:
    """Scripted, offline claims for the synthetic fixture (used only while seeding; not an AI model)."""

    def __init__(self, claims: list) -> None:
        self._claims = claims

    async def extract_legal_rights(self, document_id: str, version_id: str, pages: list[dict]):
        from app.schemas.extraction import AIExtractionResult
        from app.services.ai import UNVERIFIED_DISCLAIMER
        return AIExtractionResult(claims=self._claims, ai_model=SEED_MODEL,
                                  extraction_timestamp=datetime.now(timezone.utc), disclaimer=UNVERIFIED_DISCLAIMER)


def _claims(**changes) -> list:
    from app.schemas.extraction import ClaimSource, RawClaim
    out = []
    for field, (page, quote, value) in CLAIMS.items():
        change = changes.get(field)
        if change == "unknown":
            out.append(RawClaim(field=field, value="unknown", is_unknown=True, source=None))
            continue
        page, quote = change if isinstance(change, tuple) else (page, quote)
        out.append(RawClaim(field=field, value=value, is_unknown=False,
                            source=ClaimSource(page_number=page, supporting_passage=quote)))
    return out


async def _synthetic_anchor(asset_id: str, account: str, *, pending: bool) -> None:
    """A pending (valid) or tampered (invalid) anchor record; never submitted to any network."""
    from sqlalchemy import select

    from app.db.session import AsyncSessionLocal
    from app.config import settings
    from app.evidence_manifest import MANIFEST_SCHEMA, MANIFEST_VERSION, build_manifest, canonical_manifest, manifest_digest
    from app.models.asset import Asset
    from app.models.stellar_anchor import StellarAnchor
    from app.services.stellar_anchor import data_name_for
    from app.services.storage import StorageService

    async with AsyncSessionLocal() as db:
        asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one()
        manifest = await build_manifest(db, asset, StorageService(settings.storage_path))
        digest, canonical = manifest_digest(manifest), canonical_manifest(manifest)
        if not pending:
            canonical = canonical.replace('"jurisdiction":"GB"', '"jurisdiction":"XX"', 1)   # no longer hashes to digest
            assert hashlib.sha256(canonical.encode()).hexdigest() != digest
        now = datetime.now(timezone.utc)
        db.add(StellarAnchor(
            asset_id=asset.id, network=asset.blockchain_network, manifest_schema=MANIFEST_SCHEMA,
            manifest_version=MANIFEST_VERSION, manifest_digest=digest, manifest_json=canonical,
            anchor_account=account, data_name=data_name_for(asset.id), transaction_hash=secrets.token_hex(32),
            envelope_xdr=None, valid_until=now + timedelta(days=1), status="pending" if pending else "confirmed",
            attempts=1, requested_by=SYNTHETIC, created_at=now, updated_at=now))
        await db.commit()


async def _damage_stored_file(document_id: str, *, remove: bool) -> None:
    from sqlalchemy import select

    from app.config import settings
    from app.db.session import AsyncSessionLocal
    from app.models.document import DocumentVersion
    from app.services.storage import StorageService

    async with AsyncSessionLocal() as db:
        version = (await db.execute(select(DocumentVersion).where(DocumentVersion.document_id == document_id))).scalar_one()
        path = StorageService(settings.storage_path).full_path(version.stored_path)
    if remove:
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"\n% altered after upload (E2E fixture)\n")


async def seed(root: Path, public_showcase: bool = False) -> dict:
    from httpx import ASGITransport, AsyncClient
    from stellar_sdk import Keypair

    from app.config import settings
    from app.db.session import engine
    from app.main import app, lifespan
    from app.services.ai import get_ai_service

    database = Path(settings.database_url.split("///", 1)[-1]).resolve()
    if root.resolve() not in database.parents or settings.ai_provider != "disabled":
        raise SystemExit("refused: the configuration is not the temporary E2E environment")

    creds = _registry(root)
    anchor_file = root / "anchor_public.json"
    if public_showcase and anchor_file.exists():
        anchor_account = json.loads(anchor_file.read_text(encoding="utf-8"))["public_key"]   # kept as it is
    else:
        anchor_account = Keypair.random().public_key    # verification-only: no seed is ever created
        anchor_file.write_text(json.dumps({"network": "stellar-testnet", "public_key": anchor_account}), encoding="utf-8")
    prospectus, scanned = _fixture("trust_prospectus.pdf"), _fixture("mixed_scanned.pdf")
    from tests.pdf_fixtures import make_text_pdf
    journey_pdf = make_text_pdf(MANUAL_JOURNEY_PAGES)
    if not public_showcase:
        (root / "manual_journey.pdf").write_bytes(journey_pdf)
    h = {name: {"Authorization": f"Bearer {creds[name]}"} for name in ("admin", "issuer_a", "legal_a", "issuer_b")}
    context: dict = {
        "environment": "synthetic", "credentials": creds, "seed_model": SEED_MODEL, "assets": {},
        "fixtures": {"trust_prospectus": {"path": str(FIXTURES / "trust_prospectus.pdf"),
                                          "sha256": hashlib.sha256(prospectus).hexdigest(), "pages": 4,
                                          "size": len(prospectus)},
                     "manual_journey": {"path": str(root / "manual_journey.pdf"),
                                        "sha256": hashlib.sha256(journey_pdf).hexdigest(),
                                        "pages": len(MANUAL_JOURNEY_PAGES), "size": len(journey_pdf)}},
        "manual_journey": MANUAL_JOURNEY,
        "spare_issuer_keys": [Keypair.random().public_key for _ in range(3)],
        "display_names": {"issuer_a": "E2E Issuer Reviewer A", "legal_a": "E2E Legal Reviewer A",
                          "issuer_b": "E2E Issuer Reviewer B", "admin": "E2E Operator"},
    }

    async def ok(response, expected: int = 201) -> dict:
        if response.status_code != expected:
            raise SystemExit(f"seeding failed: {response.request.method} {response.request.url.path} "
                             f"-> HTTP {response.status_code}")
        return response.json()

    async with lifespan(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://e2e-seed") as client:
            async def asset(key: str, issuer: str, code: str, organization: str) -> dict:
                body = {"issuer_id": issuer, "blockchain_network": "stellar-testnet", "stellar_asset_code": code,
                        "stellar_issuer_public_key": Keypair.random().public_key,
                        "issuer_organization_name": organization, "asset_type": "real_estate", "jurisdiction": "GB"}
                created = await ok(await client.post("/assets", headers=h["admin"], json=body))
                context["assets"][key] = {**{k: created[k] for k in (
                    "id", "issuer_id", "stellar_asset_code", "stellar_issuer_public_key", "issuer_organization_name",
                    "blockchain_network", "asset_type", "jurisdiction", "status")}, "documents": []}
                return created

            async def upload(key: str, headers: dict, pdf: bytes, filename: str, title: str) -> dict:
                doc = await ok(await client.post(
                    f"/assets/{context['assets'][key]['id']}/documents", headers=headers,
                    data={"document_type": "prospectus", "title": title},
                    files={"file": (filename, pdf, "application/pdf")}))
                version = doc["current_version"]
                context["assets"][key]["documents"].append({
                    "id": doc["id"], "title": title, "filename": filename, "version_id": version["id"],
                    "version_number": version["version_number"], "sha256": version["sha256_hash"],
                    "size": version["file_size"], "page_count": version["text_layer"]["page_count"],
                    "pages_without_text": version["text_layer"]["pages_without_text"]})
                return doc

            async def extract(key: str, doc: dict, headers: dict, claims: list) -> dict:
                app.dependency_overrides[get_ai_service] = lambda: _SeedClaims(claims)
                try:
                    extraction = await ok(await client.post(f"/documents/{doc['id']}/extractions", headers=headers))
                finally:
                    app.dependency_overrides.pop(get_ai_service, None)
                context["assets"][key]["extraction"] = {
                    "id": extraction["id"], "document_id": doc["id"],
                    "claims": {c["field_name"]: {"known": not c["is_unknown"], "cited_page": c["source_page"],
                                                 "evidence_status": c["evidence"]["status"]} for c in extraction["claims"]}}
                return extraction

            async def approved_asset(key: str, code: str, organization: str) -> dict:
                await asset(key, ISSUER_A, code, organization)
                doc = await upload(key, h["issuer_a"], prospectus, "trust_prospectus.pdf", "Series A Prospectus (synthetic)")
                extraction = await extract(key, doc, h["issuer_a"], _claims())
                for reviewer in ("issuer_a", "legal_a"):
                    await ok(await client.post(f"/extractions/{extraction['id']}/approvals", headers=h[reviewer], json={}))
                return extraction

            # A — an approved passport: 7 grounded claims, issuer attestation and independent legal review
            extraction = await approved_asset("approved", "E2EMHT", "E2E Meridian Harbour Property Trust")
            await upload("approved", h["issuer_a"], scanned, "mixed_scanned.pdf", "Scanned annex (synthetic)")

            # A — a draft: one quotation on the wrong page, one fabricated, one unknown; never approved.
            # Plus two documents whose stored files are later removed or altered.
            await asset("draft", ISSUER_A, "E2EDRF", "E2E Meridian Draft Fund")
            doc = await upload("draft", h["issuer_a"], prospectus, "trust_prospectus.pdf", "Draft prospectus (synthetic)")
            await extract("draft", doc, h["issuer_a"], _claims(
                jurisdiction=(2, CLAIMS["jurisdiction"][1]), redemption_rights=(3, FABRICATED_QUOTE),
                voting_rights="unknown"))
            missing = await upload("draft", h["issuer_a"], prospectus, "missing_file.pdf", "Missing file (synthetic)")
            altered = await upload("draft", h["issuer_a"], prospectus, "altered_file.pdf", "Altered file (synthetic)")

            # A — the review journey: a draft of 7 grounded claims that the browser test approves
            await asset("review", ISSUER_A, "E2EREV", "E2E Meridian Review Trust")
            doc = await upload("review", h["issuer_a"], prospectus, "trust_prospectus.pdf", "Review prospectus (synthetic)")
            await extract("review", doc, h["issuer_a"], _claims())

            # A — approved passports whose anchor records are pending and tampered (synthetic records)
            if not public_showcase:
                await approved_asset("anchor_pending", "E2EPND", "E2E Meridian Pending Anchor Trust")
                await approved_asset("anchor_invalid", "E2EINV", "E2E Meridian Invalid Anchor Trust")

            # A — an approved passport of manually entered claims (Stage 10B-3): no AI service involved,
            # every quotation verified by the server, both approvals given by two reviewers
            await asset("manual", ISSUER_A, "E2EMNL", "E2E Meridian Manual Claims Trust")
            doc = await upload("manual", h["issuer_a"], prospectus, "trust_prospectus.pdf",
                               "Manual claims prospectus (synthetic)")
            manual = await ok(await client.post(f"/documents/{doc['id']}/manual-extractions", headers=h["issuer_a"], json={
                "asset_id": context["assets"]["manual"]["id"], "version_id": doc["current_version"]["id"],
                "claims": [{"field_name": field, "value": CLAIMS[field][2], "source_page": CLAIMS[field][0],
                            "supporting_passage": CLAIMS[field][1]} for field in ("jurisdiction", "insolvency_provisions")]}))
            for reviewer in ("issuer_a", "legal_a"):
                await ok(await client.post(f"/extractions/{manual['id']}/approvals", headers=h[reviewer], json={}))
            context["assets"]["manual"]["extraction"] = {
                "id": manual["id"], "document_id": doc["id"],
                "claims": {c["field_name"]: {"known": not c["is_unknown"], "cited_page": c["source_page"],
                                             "evidence_status": c["evidence"]["status"]} for c in manual["claims"]}}

            # B — another tenant
            await asset("other_tenant", ISSUER_B, "E2EHBR", "E2E Harbor Bonds")
            await upload("other_tenant", h["issuer_b"], prospectus, "trust_prospectus.pdf", "Harbor prospectus (synthetic)")

            approved = await ok(await client.get(f"/extractions/{extraction['id']}", headers=h["issuer_a"]), 200)
            context["assets"]["approved"]["approval_types"] = approved["approval_state"]["current_approval_types"]

    if not public_showcase:
        await _synthetic_anchor(context["assets"]["anchor_pending"]["id"], anchor_account, pending=True)
        await _synthetic_anchor(context["assets"]["anchor_invalid"]["id"], anchor_account, pending=False)
    await _damage_stored_file(missing["id"], remove=True)
    await _damage_stored_file(altered["id"], remove=False)
    context["damaged_documents"] = {"missing": missing["id"], "altered": altered["id"]}
    await engine.dispose()
    return context


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    context = asyncio.run(seed(root, public_showcase="--public-showcase" in sys.argv[2:]))
    (root / "context.json").write_text(json.dumps(context, indent=2), encoding="utf-8")
    print(f"seeded: {len(context['assets'])} assets")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    sys.exit(main())
