"""
Mock AI service — for automated tests.

Sends no request to the real Anthropic API.
Returns a fictional but structurally complete AIExtractionResult:
  - 4 fields extracted (with source quotes)
  - 3 fields unknown (insufficient evidence)

Thanks to this, the tests can:
  - cover both is_unknown=True and is_unknown=False.
  - verify the source reference (page_number + supporting_passage).
  - check that the disclaimer text is present.

MOCK_SOURCE_PAGES is the fictional text of the standard test document (tests/conftest.py,
created_document): page 1 contains every quotation the mock AI cites, so its known claims pass
the source-evidence check that approvals require (Stage 9B-4). Page 2 holds further passages
that the correction tests cite.
"""

from datetime import datetime, timezone

from app.enums import LegalFieldName
from app.schemas.extraction import AIExtractionResult, ClaimSource, RawClaim
from app.services.ai import UNVERIFIED_DISCLAIMER

MOCK_SOURCE_PAGES = [
    [
        "MERIDIAN PROPERTY TRUST - PROSPECTUS (FICTIONAL TEST DOCUMENT)",
        "Investors shall hold a fractional beneficial ownership interest in the Meridian Property Trust.",
        "Net rental income shall be distributed quarterly, pro-rata to token holdings.",
        "Transfer of ownership interests is restricted to accredited investors and requires prior "
        "written consent of the Trustee.",
        "This Trust Agreement is governed by the laws of the State of New York, United States of America.",
    ],
    [
        "The Trustee holds fee simple title to the Property on behalf of the Trust.",
        "Distributions are paid within thirty days after the end of each calendar quarter.",
    ],
]


class MockAIService:
    """Implements the same interface as AnthropicAIService; makes no API call."""

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
