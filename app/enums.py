import enum


class BlockchainNetwork(str, enum.Enum):
    stellar_mainnet = "stellar-mainnet"
    stellar_testnet = "stellar-testnet"


class AssetType(str, enum.Enum):
    real_estate = "real_estate"
    commodity = "commodity"
    equity = "equity"
    bond = "bond"
    fund = "fund"
    other = "other"


class AssetStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    retired = "retired"


class DocumentType(str, enum.Enum):
    prospectus = "prospectus"
    legal_opinion = "legal_opinion"
    title_deed = "title_deed"
    regulatory_filing = "regulatory_filing"
    audit_report = "audit_report"
    other = "other"


class DocumentStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class LegalFieldName(str, enum.Enum):
    """Çıkarılacak yasal hak alanlarının sabit listesi — yedi alan."""
    ownership_nature = "ownership_nature"
    income_rights = "income_rights"
    voting_rights = "voting_rights"
    redemption_rights = "redemption_rights"
    transfer_restrictions = "transfer_restrictions"
    jurisdiction = "jurisdiction"
    insolvency_provisions = "insolvency_provisions"


class ExtractionStatus(str, enum.Enum):
    """
    AI çıkarım iş akışı durumları.
    'draft'  → AI çıkardı, henüz incelenmedi
    'under_review' → inceleme devam ediyor
    'approved'     → en az bir yetkili onayladı
    Otomatik onay kesinlikle uygulanmaz.
    """
    draft = "draft"
    under_review = "under_review"
    approved = "approved"


class ReviewerRole(str, enum.Enum):
    """
    İnceleyicinin kurum içindeki rolü.
    'issuer_reviewer'            → token ihraç eden kurum çalışanı
    'independent_legal_reviewer' → bağımsız hukuk uzmanı (daha yüksek güvenilirlik)
    """
    issuer_reviewer = "issuer_reviewer"
    independent_legal_reviewer = "independent_legal_reviewer"


class ApprovalType(str, enum.Enum):
    """
    Onay türü — ihraç eden onayı ile bağımsız hukuki doğrulama ayrıştırılır.
    """
    issuer_attestation = "issuer_attestation"
    legal_verification = "legal_verification"
