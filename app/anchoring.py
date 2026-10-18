"""
Anchoring a passport's evidence manifest on Stellar Testnet, and verifying it (STELLAR_ANCHORING.md).

Submission (anchor_current_manifest):
- only for Stellar Testnet assets with a strictly valid identity and a current approved passport;
- idempotent: a manifest digest already confirmed (or pending) for the asset is never submitted
  again; a failed attempt is retried on the same record;
- the record (with the transaction hash, computed before submission) is committed BEFORE the
  transaction is submitted, so a lost response can never produce a second transaction, and a
  network error leaves it pending — never confirmed;
- one in-process lock serializes signing and submission (the account's sequence numbers).

Verification (verify_passport_anchor) is read-only. It recomputes the current manifest, checks
the stored manifest against its digest, and checks the transaction on the live network:
successful, from the configured anchoring account, memo hash = digest, and exactly one
manage_data operation "lp:<asset id>" = digest. Only then, and only if the current manifest has
the same digest, is the result 'verified'.
"""

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import security_logger
from app.enums import AnchorStatus, AnchorVerificationResult, BlockchainNetwork
from app.evidence_manifest import (
    MANIFEST_SCHEMA,
    MANIFEST_VERSION,
    ManifestUnavailable,
    asset_identity,
    build_manifest,
    canonical_manifest,
    manifest_digest,
)
from app.models.asset import Asset
from app.models.stellar_anchor import StellarAnchor
from app.schemas.stellar_anchor import (
    AnchorAssetIdentity,
    AnchorSummary,
    AnchorVerificationResponse,
    OnchainCheck,
    StellarAnchorResponse,
)
from app.services.stellar_anchor import (
    TESTNET,
    AnchorNetworkError,
    OnchainTransaction,
    StellarAnchorService,
    data_name_for,
)
from app.services.storage import StorageService
from app.validators.stellar import is_valid_stellar_asset_code, is_valid_stellar_public_key

logger = logging.getLogger("rwa_passport")

# A transaction is never included after its time bounds; allow for clock differences
EXPIRY_GRACE = timedelta(seconds=60)


@dataclass
class AnchorRefused(Exception):
    """An anchoring request that cannot be carried out; nothing was stored or submitted."""
    status_code: int
    code: str
    detail: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(moment: Optional[datetime]) -> Optional[datetime]:
    """SQLite returns stored datetimes without tzinfo; they are UTC."""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _expired(anchor: StellarAnchor) -> bool:
    valid_until = _aware(anchor.valid_until)
    return valid_until is not None and _now() > valid_until + EXPIRY_GRACE


def anchor_response(anchor: StellarAnchor, service: StellarAnchorService) -> StellarAnchorResponse:
    return StellarAnchorResponse(
        id=anchor.id,
        asset_id=anchor.asset_id,
        network=BlockchainNetwork(anchor.network),
        manifest_schema=anchor.manifest_schema,
        manifest_version=anchor.manifest_version,
        manifest_digest=anchor.manifest_digest,
        anchor_account=anchor.anchor_account,
        data_name=anchor.data_name,
        transaction_hash=anchor.transaction_hash,
        status=AnchorStatus(anchor.status),
        attempts=anchor.attempts,
        ledger=anchor.ledger,
        ledger_closed_at=_aware(anchor.ledger_closed_at),
        valid_until=_aware(anchor.valid_until),
        failure_reason=anchor.failure_reason,
        requested_by=anchor.requested_by,
        created_at=_aware(anchor.created_at),
        updated_at=_aware(anchor.updated_at),
        horizon_transaction_url=service.transaction_url(anchor.transaction_hash) if anchor.transaction_hash else None,
    )


def _check_identity(asset: Asset) -> None:
    if BlockchainNetwork(asset.blockchain_network) is not TESTNET:
        raise AnchorRefused(409, "mainnet_not_supported",
                            "Evidence anchoring is available for Stellar Testnet assets only.")
    if not (is_valid_stellar_asset_code(asset.stellar_asset_code)
            and is_valid_stellar_public_key(asset.stellar_issuer_public_key)):
        raise AnchorRefused(422, "invalid_asset_identity",
                            "The asset's Stellar code or issuer key is not a valid Stellar identifier.")


def onchain_matches(tx: OnchainTransaction, anchor: StellarAnchor, trusted_account: str) -> dict[str, bool]:
    digest = bytes.fromhex(anchor.manifest_digest)
    operations = tx.operations
    operation_matches = len(operations) == 1 and operations[0].get("type") == "manage_data" and (
        operations[0].get("name") == anchor.data_name and operations[0].get("value") is not None
        and _b64(operations[0]["value"]) == digest
    )
    return {
        "source_account_matches": tx.source_account == trusted_account == anchor.anchor_account,
        "memo_matches": tx.memo_type == "hash" and tx.memo == digest,
        "operation_matches": bool(operation_matches),
    }


def _b64(value: str) -> Optional[bytes]:
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        return None


def _apply_lookup(anchor: StellarAnchor, tx: OnchainTransaction, trusted_account: Optional[str]) -> None:
    """Moves a pending anchor to its definite state, if the network has one."""
    if tx.found:
        if not tx.successful:
            anchor.status, anchor.failure_reason = AnchorStatus.failed.value, "transaction_unsuccessful"
        elif trusted_account and all(onchain_matches(tx, anchor, trusted_account).values()):
            anchor.status, anchor.failure_reason = AnchorStatus.confirmed.value, None
            anchor.ledger, anchor.ledger_closed_at = tx.ledger, tx.closed_at
        else:
            anchor.status, anchor.failure_reason = AnchorStatus.failed.value, "onchain_mismatch"
    elif _expired(anchor):
        anchor.status, anchor.failure_reason = AnchorStatus.failed.value, "expired_not_included"
    anchor.updated_at = _now()


async def refresh_anchor(db: AsyncSession, anchor: StellarAnchor, service: StellarAnchorService) -> StellarAnchor:
    """Re-checks a pending anchor on the network; a network error changes nothing."""
    if anchor.status != AnchorStatus.pending.value or not anchor.transaction_hash:
        return anchor
    try:
        tx = await service.get_transaction(anchor.transaction_hash)
    except AnchorNetworkError:
        return anchor
    _apply_lookup(anchor, tx, service.trusted_account())
    await db.commit()
    return anchor


async def _existing(db: AsyncSession, asset: Asset, digest: str) -> Optional[StellarAnchor]:
    return (await db.execute(
        select(StellarAnchor)
        .where(StellarAnchor.asset_id == asset.id, StellarAnchor.network == asset.blockchain_network,
               StellarAnchor.manifest_digest == digest)
        .execution_options(populate_existing=True)
    )).scalar_one_or_none()


async def anchor_current_manifest(
    db: AsyncSession, asset: Asset, storage: StorageService, service: StellarAnchorService, user_id: str
) -> tuple[StellarAnchor, bool]:
    """Anchors the current manifest. Returns (anchor, True if a transaction was submitted now)."""
    _check_identity(asset)
    try:
        manifest = await build_manifest(db, asset, storage)
    except ManifestUnavailable as exc:
        raise AnchorRefused(409, "no_current_passport", f"Nothing to anchor: {exc}") from None
    digest = manifest_digest(manifest)
    service.signer()  # AnchorConfigError (not configured) before anything is stored

    async with service.lock:
        anchor = await _existing(db, asset, digest)
        if anchor is not None and anchor.status == AnchorStatus.pending.value:
            anchor = await refresh_anchor(db, anchor, service)
        if anchor is not None and anchor.status != AnchorStatus.failed.value:
            return anchor, False  # confirmed, or still pending: never a second transaction

        signed = await service.sign(asset.id, digest)
        now = _now()
        if anchor is None:
            anchor = StellarAnchor(
                asset_id=asset.id, network=asset.blockchain_network, manifest_schema=MANIFEST_SCHEMA,
                manifest_version=MANIFEST_VERSION, manifest_digest=digest,
                manifest_json=canonical_manifest(manifest), anchor_account=signed.anchor_account,
                data_name=signed.data_name, requested_by=user_id, attempts=1, created_at=now,
            )
            db.add(anchor)
        else:  # retry of a failed attempt, on the same record
            anchor.attempts += 1
            anchor.anchor_account = signed.anchor_account
        anchor.transaction_hash, anchor.envelope_xdr = signed.transaction_hash, signed.envelope_xdr
        anchor.valid_until, anchor.status = signed.valid_until, AnchorStatus.pending.value
        anchor.failure_reason, anchor.ledger, anchor.ledger_closed_at, anchor.updated_at = None, None, None, now
        try:
            await db.commit()  # recorded before submission: a lost response is resolved by refresh
        except IntegrityError:
            await db.rollback()  # another process recorded this digest first
            existing = await _existing(db, asset, digest)
            if existing is None:
                raise
            return existing, False

        outcome = await service.submit(signed.envelope_xdr)
        anchor.status, anchor.failure_reason, anchor.updated_at = outcome.status.value, outcome.failure_reason, _now()
        if outcome.status is AnchorStatus.confirmed:
            anchor.ledger = outcome.ledger
            try:  # the ledger close time, and an independent read-back of what was included
                tx = await service.get_transaction(signed.transaction_hash)
                if tx.found:
                    _apply_lookup(anchor, tx, signed.anchor_account)
            except AnchorNetworkError:
                pass
        await db.commit()
    security_logger.info(
        "anchor.submitted asset=%s digest=%s tx=%s status=%s attempt=%d",
        asset.id, digest[:16], signed.transaction_hash, anchor.status, anchor.attempts,
    )
    return anchor, True


def _choose(anchors: list[StellarAnchor], current_digest: Optional[str]) -> Optional[StellarAnchor]:
    """The anchor of the current evidence if any (confirmed first), else the latest confirmed, else the latest."""
    if not anchors:
        return None
    by_time = sorted(anchors, key=lambda a: _aware(a.created_at))
    status_rank = {AnchorStatus.confirmed.value: 0, AnchorStatus.pending.value: 1, AnchorStatus.failed.value: 2}
    matching = [a for a in by_time if a.manifest_digest == current_digest]
    if matching:
        return min(matching, key=lambda a: status_rank[a.status])
    confirmed = [a for a in by_time if a.status == AnchorStatus.confirmed.value]
    return (confirmed or by_time)[-1]


def _record_valid(anchor: StellarAnchor, asset: Asset) -> bool:
    if hashlib.sha256(anchor.manifest_json.encode("utf-8")).hexdigest() != anchor.manifest_digest:
        return False
    try:
        committed = json.loads(anchor.manifest_json)["asset"]
    except (ValueError, KeyError, TypeError):
        return False
    return (
        committed.get("asset_id") == asset.id
        and committed.get("network") == asset.blockchain_network
        and committed.get("asset_code") == asset.stellar_asset_code
        and committed.get("asset_issuer") == asset.stellar_issuer_public_key
        and anchor.data_name == data_name_for(asset.id)
    )


async def verify_passport_anchor(
    db: AsyncSession, asset: Asset, storage: StorageService, service: StellarAnchorService
) -> AnchorVerificationResponse:
    """Read-only verification against the live network; nothing unconfirmed is ever 'verified'."""
    try:
        current: Optional[str] = manifest_digest(await build_manifest(db, asset, storage))
    except ManifestUnavailable:
        current = None
    anchors = (await db.execute(
        select(StellarAnchor).where(StellarAnchor.asset_id == asset.id,
                                    StellarAnchor.network == asset.blockchain_network)
    )).scalars().all()
    anchor = _choose(list(anchors), current)
    onchain = OnchainCheck(checked=False)
    trusted = service.trusted_account()

    if anchor is None:
        result = AnchorVerificationResult.not_anchored
    elif trusted is None:
        result = AnchorVerificationResult.anchoring_not_configured
    elif not _record_valid(anchor, asset):
        result = AnchorVerificationResult.anchor_record_invalid
    elif anchor.status == AnchorStatus.failed.value or not anchor.transaction_hash:
        result = AnchorVerificationResult.failed
    else:
        result = await _check_network(anchor, current, trusted, service, onchain)

    if result is not AnchorVerificationResult.verified and anchor is not None:
        logger.info("anchor.verification asset=%s result=%s", asset.id, result.value)
    return AnchorVerificationResponse(
        asset=AnchorAssetIdentity(**{k: v for k, v in asset_identity(asset).items() if k != "network_passphrase"}),
        manifest_schema=MANIFEST_SCHEMA,
        manifest_version=MANIFEST_VERSION,
        current_passport_available=current is not None,
        current_manifest_digest=current,
        anchor=None if anchor is None else AnchorSummary(
            manifest_digest=anchor.manifest_digest, transaction_hash=anchor.transaction_hash,
            status=AnchorStatus(anchor.status), anchor_account=anchor.anchor_account, data_name=anchor.data_name,
            ledger=anchor.ledger, ledger_closed_at=_aware(anchor.ledger_closed_at),
            anchored_at=_aware(anchor.created_at),
            horizon_transaction_url=service.transaction_url(anchor.transaction_hash) if anchor.transaction_hash else None,
        ),
        evidence_matches_anchor=anchor is not None and current is not None and anchor.manifest_digest == current,
        onchain=onchain,
        result=result,
        verified=result is AnchorVerificationResult.verified,
        committed_manifest_canonical_json=anchor.manifest_json if anchor is not None else None,
        checked_at=_now(),
    )


async def _check_network(
    anchor: StellarAnchor, current: Optional[str], trusted: str, service: StellarAnchorService, onchain: OnchainCheck
) -> AnchorVerificationResult:
    onchain.checked = True
    try:
        onchain.network_passphrase_matches = await service.network_matches()
        if not onchain.network_passphrase_matches:
            onchain.error = "network_passphrase_mismatch"
            return AnchorVerificationResult.network_unavailable
        tx = await service.get_transaction(anchor.transaction_hash)
    except AnchorNetworkError as exc:
        onchain.error = str(exc)
        return AnchorVerificationResult.network_unavailable
    onchain.transaction_found = tx.found
    if not tx.found:
        if anchor.status == AnchorStatus.pending.value:
            return AnchorVerificationResult.failed if _expired(anchor) else AnchorVerificationResult.pending
        onchain.error = "transaction_not_returned_by_the_network"
        return AnchorVerificationResult.transaction_unavailable
    onchain.successful, onchain.ledger, onchain.ledger_closed_at = tx.successful, tx.ledger, tx.closed_at
    if not tx.successful:
        return AnchorVerificationResult.failed
    checks = onchain_matches(tx, anchor, trusted)
    onchain.source_account_matches = checks["source_account_matches"]
    onchain.memo_matches = checks["memo_matches"]
    onchain.operation_matches = checks["operation_matches"]
    if not all(checks.values()):
        return AnchorVerificationResult.onchain_mismatch
    if current != anchor.manifest_digest:
        return AnchorVerificationResult.evidence_changed
    return AnchorVerificationResult.verified
