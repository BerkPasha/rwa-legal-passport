"""
Mock AI servisi — otomatik testler için.

Gerçek Anthropic API'ye istek atmaz.
Kurgusal ama yapısal olarak eksiksiz bir AIExtractionResult döndürür:
  - 4 alan çıkarıldı (kaynak alıntısıyla)
  - 3 alan bilinmiyor (kanıt yetersiz)

Bu yeterlilik sayesinde testler:
  - is_unknown=True / False iki durumu birden kapsar.
  - Kaynak referansı (page_number + supporting_passage) doğrulayabilir.
  - Sorumluluk reddi metninin varlığını kontrol edebilir.
"""

from datetime import datetime, timezone

from app.enums import LegalFieldName
from app.schemas.extraction import AIExtractionResult, ClaimSource, RawClaim
from app.services.ai import UNVERIFIED_DISCLAIMER


class MockAIService:
    """AnthropicAIService ile aynı arayüzü uygular; API çağrısı yapmaz."""

    async def extract_legal_rights(
        self,
        document_id: str,
        version_id: str,
        pages: list[dict],
    ) -> AIExtractionResult:
        return AIExtractionResult(
            claims=[
                RawClaim(
                    field=LegalFieldName.ownership_nature,
                    value=(
                        "Fractional beneficial ownership interest "
                        "in the underlying real estate trust."
                    ),
                    is_unknown=False,
                    source=ClaimSource(
                        page_number=1,
                        supporting_passage=(
                            "Investors shall hold a fractional beneficial ownership "
                            "interest in the Meridian Property Trust."
                        ),
                    ),
                ),
                RawClaim(
                    field=LegalFieldName.income_rights,
                    value=(
                        "Quarterly distributions of net rental income, "
                        "pro-rata to token holdings."
                    ),
                    is_unknown=False,
                    source=ClaimSource(
                        page_number=1,
                        supporting_passage=(
                            "Net rental income shall be distributed quarterly, "
                            "pro-rata to token holdings."
                        ),
                    ),
                ),
                RawClaim(
                    field=LegalFieldName.voting_rights,
                    value="unknown",
                    is_unknown=True,
                    source=None,
                ),
                RawClaim(
                    field=LegalFieldName.redemption_rights,
                    value="unknown",
                    is_unknown=True,
                    source=None,
                ),
                RawClaim(
                    field=LegalFieldName.transfer_restrictions,
                    value=(
                        "Transfer restricted to accredited investors only; "
                        "prior written trustee consent required."
                    ),
                    is_unknown=False,
                    source=ClaimSource(
                        page_number=1,
                        supporting_passage=(
                            "Transfer of ownership interests is restricted to accredited "
                            "investors and requires prior written consent of the Trustee."
                        ),
                    ),
                ),
                RawClaim(
                    field=LegalFieldName.jurisdiction,
                    value="State of New York, United States of America.",
                    is_unknown=False,
                    source=ClaimSource(
                        page_number=1,
                        supporting_passage=(
                            "This Trust Agreement is governed by the laws of the "
                            "State of New York, United States of America."
                        ),
                    ),
                ),
                RawClaim(
                    field=LegalFieldName.insolvency_provisions,
                    value="unknown",
                    is_unknown=True,
                    source=None,
                ),
            ],
            ai_model="mock-v1",
            extraction_timestamp=datetime.now(timezone.utc),
            disclaimer=UNVERIFIED_DISCLAIMER,
        )
