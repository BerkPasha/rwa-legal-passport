from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.enums import DocumentStatus, DocumentType


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
    # Güncel versiyonun özeti; listede None, detayda dolu gelir
    current_version: Optional[DocumentVersionResponse] = None

    model_config = ConfigDict(from_attributes=True)


class DocumentTextResponse(BaseModel):
    """Güncel versiyondan çıkarılan sayfa metinleri."""
    document_id: str
    version_number: int
    pages: list[DocumentPageResponse]
