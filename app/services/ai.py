"""
AI Yasal Hak Çıkarım Servisi.

Anthropic Messages API'sini kullanarak yüklü yasal belgelerden
yedi adet yapısal hak alanını çıkarır.

GÜVENLİK KURALLARI:
- API anahtarı asla loglanmaz veya hata mesajlarında gösterilmez.
- Tüm çıktılar "doğrulanmamış taslak" olarak işaretlenir.
- Yasal geçerlilik hiçbir zaman onaylanmaz.
- Kanıt yetersizse "unknown" döndürülür; uydurma yapılmaz.
"""

import json
import re
from datetime import datetime, timezone
from typing import Optional

import anthropic
from pydantic import BaseModel, ValidationError

from app.enums import LegalFieldName
from app.schemas.extraction import AIExtractionResult, ClaimSource, RawClaim

# ── Sabitler ──────────────────────────────────────────────────────────────────

UNVERIFIED_DISCLAIMER = (
    "DOĞRULANMAMIŞ AI TASLAĞI — Bu çıkarım bir yapay zeka modeli tarafından "
    "üretilmiştir ve nitelikli bir hukuk profesyoneli tarafından incelenmemiş ya da "
    "onaylanmamıştır. Bağımsız uzman doğrulaması yapılmadan herhangi bir hukuki, "
    "yatırım veya uyumluluk kararında kullanılamaz."
)

_SYSTEM_PROMPT = """\
You are a legal document analyst for tokenized real-world asset (RWA) platforms.

Extract seven legal rights fields from the document provided and return ONLY a valid JSON object.

MANDATORY RULES — follow exactly:
1. Extract ONLY information explicitly stated in the document text.
2. Never infer, assume, or invent legal provisions.
3. If a field is absent or evidence is insufficient, set value to the exact string "unknown" and is_unknown to true.
4. For every known field (is_unknown = false), include a verbatim quote as supporting_passage.
5. Return ONLY valid JSON — no markdown, no preamble, no explanation.

Fields to extract:
  ownership_nature      — Nature of investor ownership rights
  income_rights         — Income distribution and payment rights
  voting_rights         — Voting or governance rights
  redemption_rights     — Redemption terms and conditions
  transfer_restrictions — Restrictions on transfer of ownership
  jurisdiction          — Applicable legal jurisdiction
  insolvency_provisions — Insolvency or bankruptcy-related provisions

Required JSON structure (include all seven fields):
{
  "claims": [
    {
      "field": "ownership_nature",
      "value": "<extracted text>",
      "is_unknown": false,
      "source": { "page_number": 1, "supporting_passage": "<exact verbatim quote>" }
    },
    {
      "field": "voting_rights",
      "value": "unknown",
      "is_unknown": true,
      "source": null
    }
  ]
}\
"""


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────


def _format_pages(pages: list[dict]) -> str:
    """Sayfa listesini modele gönderilecek metin bloğuna dönüştürür."""
    if not pages:
        return "(belge çıkarılabilir metin içermiyor)"
    blocks = [
        f"=== SAYFA {p['page_number']} ===\n{p['text_content'] or '(boş sayfa)'}"
        for p in pages
    ]
    return "\n\n".join(blocks)


def _extract_json_from_text(text: str) -> dict:
    """Yanıttaki markdown kod bloklarını temizler ve JSON ayrıştırır."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


class _RawAIResponse(BaseModel):
    """AI'ın döndürdüğü ham JSON yapısının doğrulama şeması."""
    claims: list[RawClaim]


def _parse_and_complete(raw_text: str, model: str) -> AIExtractionResult:
    """
    AI yanıt metnini ayrıştırır, eksik alanları 'unknown' ile tamamlar,
    doğrulanmış bir AIExtractionResult döner.
    """
    try:
        raw_dict = _extract_json_from_text(raw_text)
        parsed = _RawAIResponse.model_validate(raw_dict)
    except (json.JSONDecodeError, ValidationError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"AI geçersiz bir yanıt formatı döndürdü: {exc}"
        ) from exc

    # Eksik alanları 'unknown' ile tamamla
    present = {c.field for c in parsed.claims}
    for field in LegalFieldName:
        if field not in present:
            parsed.claims.append(
                RawClaim(field=field, value="unknown", is_unknown=True, source=None)
            )

    return AIExtractionResult(
        claims=parsed.claims,
        ai_model=model,
        extraction_timestamp=datetime.now(timezone.utc),
        disclaimer=UNVERIFIED_DISCLAIMER,
    )


# ── Servis sınıfı ─────────────────────────────────────────────────────────────


class AnthropicAIService:
    """
    Anthropic claude modelini kullanarak yasal hak çıkarımı yapar.
    Testlerde bu sınıf mock ile değiştirilir — gerçek API çağrısı yapılmaz.
    """

    def __init__(self, api_key: str, model: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)
        self._model = model

    async def extract_legal_rights(
        self,
        document_id: str,
        version_id: str,
        pages: list[dict],
    ) -> AIExtractionResult:
        """
        Belge sayfalarından yedi yasal hak alanını çıkarır.
        Hata durumunda RuntimeError fırlatır (API anahtarı asla dahil edilmez).
        """
        user_message = (
            f"Extract legal rights from this document.\n\n"
            f"Document ID: {document_id}\n\n"
            f"{_format_pages(pages)}"
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.AuthenticationError:
            raise RuntimeError(
                "AI servisi: kimlik doğrulama başarısız. API anahtarı yapılandırmasını kontrol edin."
            )
        except anthropic.RateLimitError:
            raise RuntimeError(
                "AI servisi: istek sınırı aşıldı. Lütfen daha sonra tekrar deneyin."
            )
        except anthropic.APIError as exc:
            # Dahili hata ayrıntıları istemciye iletilmez
            raise RuntimeError(
                f"AI servisi: istek başarısız oldu (HTTP {exc.status_code})."
            ) from exc

        return _parse_and_complete(response.content[0].text, self._model)


# ── FastAPI bağımlılığı ───────────────────────────────────────────────────────


def get_ai_service() -> AnthropicAIService:
    """Varsayılan AI servisi — testlerde mock ile değiştirilir."""
    from app.config import settings
    return AnthropicAIService(
        api_key=settings.anthropic_api_key,
        model=settings.ai_model,
    )
