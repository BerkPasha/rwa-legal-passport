"""
Versioned evidence manifest of a Legal Passport (STELLAR_ANCHORING.md).

The manifest is a deterministic description of exactly what the passport currently serves:
- the Stellar asset identity (network and passphrase, asset code, issuer, Stellar asset type);
- the material asset information and its revision;
- per contributing document: the approved document version (id, number, SHA-256 of the stored
  file), the extraction, the format 2 evidence record of every claim (value, cited page,
  quotation, evidence status, SHA-256 of the cited page text and of the quotation, correction
  identity) and the counted current approvals (type, role, time and binding digest);
- the documents excluded from the passport and why;
- the verification indicators (legacy verification_level, verification_status, attestation
  flags and counts).

It contains no reviewer identities, credentials or document files. Its digest is
SHA-256(canonical JSON) as lowercase hex, canonical JSON being sorted keys, no insignificant
whitespace, UTF-8 (app/approval_integrity.canonical_json). Only the digest is committed on-chain;
the manifest itself stays off-chain. The digest changes whenever any committed element changes
(a new document version, a correction, an approval, an exclusion, asset information); it does not
depend on the time of reading. The manifest proves nothing about legal validity.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.approval_integrity import canonical_json, evidence_snapshot_v2, sha256_digest
from app.enums import AssetStatus, BlockchainNetwork
from app.models.asset import Asset
from app.services.storage import StorageService
from app.validators.stellar import stellar_asset_type

MANIFEST_SCHEMA = "rwa-legal-passport/evidence-manifest"
MANIFEST_VERSION = 1

NETWORK_PASSPHRASES: dict[BlockchainNetwork, str] = {
    BlockchainNetwork.stellar_testnet: "Test SDF Network ; September 2015",
    BlockchainNetwork.stellar_mainnet: "Public Global Stellar Network ; September 2015",
}


class ManifestUnavailable(Exception):
    """The asset has no current approved passport: there is nothing to commit."""


def _iso(moment: datetime) -> str:
    """UTC, microseconds, no offset: identical whether or not the database returned tzinfo."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.isoformat(timespec="microseconds")


def asset_identity(asset: Asset) -> dict:
    """The on-chain identity a passport is associated with (an identifier, not proof of ownership)."""
    network = BlockchainNetwork(asset.blockchain_network)
    return {
        "identifier_type": "stellar_classic_asset",
        "network": network.value,
        "network_passphrase": NETWORK_PASSPHRASES[network],
        "asset_code": asset.stellar_asset_code,
        "asset_issuer": asset.stellar_issuer_public_key,
        "stellar_asset_type": stellar_asset_type(asset.stellar_asset_code),
    }


async def build_manifest(db: AsyncSession, asset: Asset, storage: StorageService) -> dict:
    """The manifest of what the passport serves now. Raises ManifestUnavailable without one."""
    # Imported here: the passport router builds the very same view (no second implementation)
    from app.routers import passport as passport_view

    if asset.status != AssetStatus.active.value:
        raise ManifestUnavailable("The asset is not active.")
    sources, excluded, _ = await passport_view._evaluate_asset(db, asset, storage)
    if not sources:
        raise ManifestUnavailable("The asset has no current approved passport.")
    legal_rights = passport_view._legal_rights(sources)
    summary = passport_view._verification_summary(legal_rights, sources)

    documents = []
    for source in sorted(sources, key=lambda s: s.ctx.document.id):
        documents.append({
            "document_id": source.ctx.document.id,
            "document_type": source.ctx.document.document_type,
            "extraction_id": source.ctx.extraction.id,
            "evidence": evidence_snapshot_v2(source.ctx),
            "approvals": [
                {
                    "approval_type": a.approval_type,
                    "reviewer_role": a.reviewer_role,
                    "approved_at": _iso(a.approved_at),
                    "binding_digest": a.binding_digest,
                }
                for a in sorted(source.approvals, key=lambda a: (_iso(a.approved_at), a.binding_digest))
            ],
        })
    return {
        "schema": MANIFEST_SCHEMA,
        "schema_version": MANIFEST_VERSION,
        "asset": {
            **asset_identity(asset),
            "asset_id": asset.id,
            "issuer_organization": asset.issuer_organization_name,
            "rwa_asset_type": asset.asset_type,
            "jurisdiction": asset.jurisdiction,
            "status": asset.status,
            "material_revision": asset.material_revision,
        },
        "documents": documents,
        "excluded_documents": sorted(
            ({"document_id": d.document_id, "reasons": [r.value for r in d.reasons]} for d in excluded),
            key=lambda d: d["document_id"],
        ),
        "verification": {
            "verification_level": passport_view._verification_level(legal_rights).value,
            "verification_status": passport_view._verification_status(legal_rights).value,
            "issuer_attestation_recorded": summary.issuer_attestation_recorded,
            "legal_reviewer_approval_recorded": summary.legal_reviewer_approval_recorded,
            "source_verified_field_count": summary.source_verified_field_count,
            "independently_attested_field_count": summary.independently_attested_field_count,
            "unknown_field_count": summary.unknown_field_count,
            "conflicting_field_count": summary.conflicting_field_count,
        },
    }


def manifest_digest(manifest: dict) -> str:
    """SHA-256 (lowercase hex) of the canonical JSON of the manifest."""
    return sha256_digest(manifest)


def canonical_manifest(manifest: dict) -> str:
    return canonical_json(manifest)
