"""
Kurumsal Yasal Pasaport yanıt şemaları.

Her alan OpenAPI dokümantasyonu için description içerir.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.enums import BlockchainNetwork


class AssetSummary(BaseModel):
    """Tokenize edilmiş varlığın tanımlayıcı bilgileri."""
    asset_id: str = Field(description="İç veritabanı tanımlayıcısı.")
    asset_code: str = Field(description="Stellar varlık kodu (örn. MRPROP).")
    issuer_key: str = Field(description="İhraçcı Stellar genel anahtarı.")
    network: BlockchainNetwork = Field(description="Bağlı Stellar ağı.")
    issuer_organization: str = Field(description="Token ihraç eden kuruluşun adı.")
    asset_type: str = Field(description="Varlık kategorisi (real_estate, bond vb.).")
    jurisdiction: str = Field(description="Yasal yetki alanı.")


class LegalRightEntry(BaseModel):
    """
    Tek bir yasal hak veya kısıtlama kaydı.

    verified=True: değer bilinmekte ve destekleyici kaynak kanıt mevcuttur.
    verified=False: kanıt yetersiz veya alan bilinmiyor.
    """
    field_name: str = Field(description="Yasal alan adı (örn. ownership_nature).")
    value: str = Field(description="Çıkarılan ve onaylanan değer; bilinmiyorsa 'unknown'.")
    is_unknown: bool = Field(description="True ise belgede yeterli kanıt bulunamadı.")
    verified: bool = Field(
        description="True ise değer hem biliniyor hem de kaynak kanıtı mevcuttur."
    )
    source_page: Optional[int] = Field(
        default=None, description="Belgedeki kaynak sayfa numarası."
    )
    supporting_passage: Optional[str] = Field(
        default=None,
        description="Talebi destekleyen belgeden alıntı; yalnızca verified=True ise dolu.",
    )


class ApprovalEntry(BaseModel):
    """Bir inceleyicinin onay kaydı."""
    approval_type: str = Field(
        description="issuer_attestation (ihraçcı) veya legal_verification (bağımsız hukuk)."
    )
    reviewer_role: str = Field(description="İnceleyicinin rolü.")
    approved_at: datetime = Field(description="Onay zaman damgası (UTC).")
    notes: Optional[str] = Field(default=None, description="İnceleyicinin isteğe bağlı notları.")


class DocumentReference(BaseModel):
    """Çıkarımın dayandığı kaynak belgenin meta verisi."""
    document_id: str = Field(description="İç belge tanımlayıcısı.")
    title: str = Field(description="Belge başlığı.")
    document_type: str = Field(description="Belge kategorisi (prospectus, legal_opinion vb.).")
    version_number: int = Field(description="Çıkarımın yapıldığı belge versiyonu.")


class ExtractionMetadata(BaseModel):
    """AI çıkarımına ait teknik meta veriler."""
    extraction_id: str = Field(description="İç çıkarım tanımlayıcısı.")
    ai_model: str = Field(description="Çıkarımı gerçekleştiren AI modeli.")
    extraction_timestamp: datetime = Field(description="AI çıkarım zaman damgası (UTC).")
    passport_version: int = Field(
        description="Bu varlık için kaçıncı onaylı pasaport versiyonu olduğu."
    )
    effective_date: datetime = Field(
        description="En son onay zaman damgası — geçerlilik tarihi olarak kullanılır."
    )


class LegalPassportResponse(BaseModel):
    """
    Kurumsal Yasal Pasaport — onaylanmış yasal hak özeti.

    UYARI: Bu pasaport yalnızca iç inceleme sürecini yansıtır.
    Yasal tavsiye, düzenleyici onay veya yatırım tavsiyesi değildir.
    Tüm bilgiler bağımsız hukuki danışman tarafından doğrulanmalıdır.
    """
    asset: AssetSummary
    legal_rights: list[LegalRightEntry]
    approvals: list[ApprovalEntry]
    source_document: DocumentReference
    extraction_metadata: ExtractionMetadata
    # Her yanıtta zorunlu — pasaport ≠ yasal sertifika
    disclaimer: str = Field(description="Hukuki sorumluluk reddi — her yanıtta zorunlu.")
    retrieved_at: datetime = Field(description="Bu pasaportun alındığı zaman damgası (UTC).")
