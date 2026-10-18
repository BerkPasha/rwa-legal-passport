"""
Role-based permission model.

The role of each Bearer user determines the operations (Permission) it may perform.
Resource-level issuer (tenant) scope is enforced separately in app/authorization.py:
even an authorized role can only reach data of the issuers within its scope.

Operations of institutional API keys (InstitutionalOperation) are separate;
API keys cannot access any internal (Bearer) endpoint.
"""

import enum

from app.enums import UserRole


class Permission(str, enum.Enum):
    asset_create = "asset:create"
    asset_read = "asset:read"
    asset_update = "asset:update"
    document_upload = "document:upload"
    document_read = "document:read"
    extraction_run = "extraction:run"
    extraction_read = "extraction:read"
    review_start = "review:start"
    claim_correct = "claim:correct"
    approval_create = "approval:create"
    audit_read = "audit:read"
    anchor_create = "anchor:create"  # Stellar Testnet evidence anchoring (STELLAR_ANCHORING.md)


class InstitutionalOperation(str, enum.Enum):
    """Operations that can be granted to an institutional API key."""
    passport_read = "passport:read"


ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    # Platform operator: manages the asset registry and can read for support;
    # cannot review, correct or approve.
    UserRole.platform_admin: frozenset({
        Permission.asset_create,
        Permission.asset_read,
        Permission.asset_update,
        Permission.document_upload,
        Permission.document_read,
        Permission.extraction_run,
        Permission.extraction_read,
        Permission.audit_read,
        Permission.anchor_create,
    }),
    # Issuer reviewer: uploads its own documents, starts AI extraction,
    # reviews and gives the issuer attestation (issuer_attestation).
    UserRole.issuer_reviewer: frozenset({
        Permission.asset_read,
        Permission.document_upload,
        Permission.document_read,
        Permission.extraction_run,
        Permission.extraction_read,
        Permission.review_start,
        Permission.claim_correct,
        Permission.approval_create,
        Permission.audit_read,
        Permission.anchor_create,
    }),
    # Independent legal expert: reads, reviews and corrects the documents of the
    # assigned issuers and gives the legal verification (legal_verification). Cannot
    # upload documents or run the AI.
    UserRole.independent_legal_reviewer: frozenset({
        Permission.asset_read,
        Permission.document_read,
        Permission.extraction_read,
        Permission.review_start,
        Permission.claim_correct,
        Permission.approval_create,
        Permission.audit_read,
    }),
}
