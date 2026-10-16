from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.enums import DocumentStatus, DocumentType

# What the served text is, and what it is not (Stage 9B-4, step 8)
SOURCE_TEXT_ORIGIN = "pdf_text_layer_extracted_at_upload"
SOURCE_TEXT_NOTICE = (
    "Text extracted from the PDF text layer of exactly this document version when it was uploaded; "
    "pages without a text layer are empty (no OCR). It is evidence material only: it is not a legal "
    "interpretation and does not establish legal validity, ownership or enforceability."
)


class DocumentPageResponse(BaseModel):
    page_number: int
    text_content: str

    model_config = ConfigDict(from_attributes=True)


class DocumentVersionResponse(BaseModel):
    id: str
    document_id: str
    version_number: int
    original_filename: str
    file_size: int
    sha256_hash: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentResponse(BaseModel):
    id: str
    asset_id: str
    document_type: DocumentType
    title: str
    status: DocumentStatus
    current_version_number: int
    created_at: datetime
    updated_at: datetime
    # Summary of the current version; None in list responses, populated in detail responses
    current_version: Optional[DocumentVersionResponse] = None

    model_config = ConfigDict(from_attributes=True)


class DocumentTextResponse(BaseModel):
    """
    Page texts extracted from the CURRENT version (whatever version that is at the time of the
    request). For the text of a specific version use GET /documents/{id}/versions/{n}/text.
    """
    document_id: str
    version_number: int
    version_id: str = Field(description="Identifier of the version these pages belong to (Stage 9B-4, step 8).")
    pages: list[DocumentPageResponse]


class DocumentVersionTextResponse(BaseModel):
    """
    Page texts of exactly one, explicitly requested document version (Stage 9B-4, step 8).
    Never another version's text: the pages are those recorded for this version id at upload.
    """
    document_id: str
    document_status: DocumentStatus
    version_id: str
    version_number: int
    version_created_at: datetime
    sha256_hash: str = Field(description="SHA-256 of this version's PDF file, recorded at upload.")
    is_current_version: bool = Field(
        description="False if a newer version exists; the text is still exactly this version's."
    )
    current_version_number: int
    page_count: int
    source_text_sha256: str = Field(
        description=(
            "SHA-256 over this version's page numbers and texts — the value an approval's evidence "
            "record (snapshot format 2, source.source_text_sha256) binds. An unkeyed digest: it "
            "identifies content and is not a signature."
        )
    )
    text_origin: str = Field(default=SOURCE_TEXT_ORIGIN, description="How the text was obtained.")
    notice: str = Field(default=SOURCE_TEXT_NOTICE, description="What this text is and is not.")
    pages: list[DocumentPageResponse] = Field(description="Pages 1..page_count of this version.")
