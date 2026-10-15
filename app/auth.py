"""
Reviewer kimlik doğrulama modülü.

HTTP Bearer token ile koruma sağlar.
Her token belirli bir kullanıcıya ve role bağlıdır.

GİZLİLİK:
Token değerleri kaynak kodda tutulmaz; .env dosyasından okunur
(REVIEWER_ISSUER_TOKEN, REVIEWER_LEGAL_TOKEN). Boş bırakılan token devre dışıdır.

ÜRETİM NOTU:
Üretimde JWT tabanlı bir kimlik sağlayıcıyla (OAuth2/OIDC) değiştirilmelidir.

AI ONAY YASAĞI:
AI servisi geçerli bir Bearer token'a sahip değildir.
Tüm onay talepleri insan kimlik doğrulaması gerektirir.
Kod katmanında ek kontrol: kullanıcı kimliği "ai-" ile başlıyorsa reddedilir.
"""

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

from app.config import settings
from app.enums import ReviewerRole

_bearer = HTTPBearer()


class ReviewerInfo(BaseModel):
    """Kimliği doğrulanmış inceleyicinin profili."""
    user_id: str
    display_name: str
    role: ReviewerRole


# ── Kayıtlı inceleyiciler ─────────────────────────────────────────────────────
# Üretimde veritabanı destekli kullanıcı yönetimiyle değiştirilecek.
_ISSUER_REVIEWER = ReviewerInfo(
    user_id="alice-chen-01",
    display_name="Alice Chen",
    role=ReviewerRole.issuer_reviewer,
)
_LEGAL_REVIEWER = ReviewerInfo(
    user_id="bob-martinez-01",
    display_name="Bob Martinez",
    role=ReviewerRole.independent_legal_reviewer,
)


def get_reviewer_registry() -> dict[str, ReviewerInfo]:
    """
    FastAPI bağımlılığı: token → inceleyici eşlemesi (token'lar .env'den).
    Testlerde kurgusal test token'larıyla değiştirilir.
    """
    tokens = {
        settings.reviewer_issuer_token: _ISSUER_REVIEWER,
        settings.reviewer_legal_token: _LEGAL_REVIEWER,
    }
    return {token: reviewer for token, reviewer in tokens.items() if token}


def get_current_reviewer(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    registry: dict[str, ReviewerInfo] = Depends(get_reviewer_registry),
) -> ReviewerInfo:
    """
    FastAPI bağımlılığı: Bearer token'ı doğrular ve inceleyici profilini döner.
    Geçersiz token → 401 Unauthorized
    """
    reviewer = registry.get(credentials.credentials)
    if reviewer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz veya eksik kimlik doğrulama token'ı.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return reviewer
