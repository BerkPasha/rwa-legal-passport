"""
Kurumsal istemci API anahtarı kimlik doğrulaması.

HTTP başlığı: X-API-Key
Anahtarlar belirli bir kurumsal istemciye bağlıdır.

ÜRETİM NOTU:
Anahtarlar kaynak kodda tutulmaz; .env dosyasından okunur
(INSTITUTIONAL_API_KEY_ALPHA, INSTITUTIONAL_API_KEY_BETA). Boş bırakılan anahtar devre dışıdır.
Üretimde güvenli bir anahtar yönetim sistemi (KMS/Vault) kullanılmalıdır.

GİZLİLİK:
API anahtarı hiçbir zaman loglanmaz; yalnızca son 4 karakteri ipucu olarak kullanılabilir.
"""

from typing import Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

from app.config import settings

_api_key_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)


class InstitutionalClient(BaseModel):
    """Kimliği doğrulanmış kurumsal istemci profili."""
    client_id: str
    organization: str
    tier: str  # "read" — okuma yetkisi; genişleme için yer tutucu


# ── Kayıtlı kurumsal istemciler ───────────────────────────────────────────────
_ALPHA_CLIENT = InstitutionalClient(
    client_id="meridian-capital-001",
    organization="Meridian Capital Partners",
    tier="read",
)
_BETA_CLIENT = InstitutionalClient(
    client_id="apex-legal-research-001",
    organization="Apex Legal Research Inc.",
    tier="read",
)


def get_client_registry() -> dict[str, InstitutionalClient]:
    """
    FastAPI bağımlılığı: API anahtarı → kurumsal istemci eşlemesi (anahtarlar .env'den).
    Testlerde kurgusal test anahtarlarıyla değiştirilir.
    """
    keys = {
        settings.institutional_api_key_alpha: _ALPHA_CLIENT,
        settings.institutional_api_key_beta: _BETA_CLIENT,
    }
    return {key: client for key, client in keys.items() if key}


def get_institutional_client(
    api_key: Optional[str] = Security(_api_key_scheme),
    registry: dict[str, InstitutionalClient] = Depends(get_client_registry),
) -> InstitutionalClient:
    """
    FastAPI bağımlılığı: X-API-Key başlığını doğrular.
    Başlık eksikse  → 403 Forbidden
    Geçersiz anahtar → 401 Unauthorized
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="X-API-Key başlığı gereklidir.",
        )
    client = registry.get(api_key)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Geçersiz API anahtarı.",
        )
    return client
