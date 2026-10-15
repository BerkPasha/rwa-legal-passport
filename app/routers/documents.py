"""
Yasal belge yönetimi endpointleri.

POST /assets/{asset_id}/documents          — Yeni belge yükle
GET  /assets/{asset_id}/documents          — Varlığa ait belgeleri listele
GET  /documents/{document_id}              — Belge meta verisi
POST /documents/{document_id}/versions     — Yeni versiyon yükle
GET  /documents/{document_id}/versions     — Versiyon geçmişi
GET  /documents/{document_id}/text         — Çıkarılan sayfa metinleri
GET  /documents/{document_id}/download     — Dosyayı indir
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.enums import DocumentType
from app.models.asset import Asset
from app.models.document import Document, DocumentPage, DocumentVersion
from app.schemas.document import (
    DocumentPageResponse,
    DocumentResponse,
    DocumentTextResponse,
    DocumentVersionResponse,
)
from app.services.pdf import calculate_sha256, extract_pages, is_valid_pdf_content
from app.services.storage import StorageService, get_storage

router = APIRouter(tags=["Documents"])

_ALLOWED_EXTENSION = ".pdf"


# ── Yardımcı fonksiyonlar ─────────────────────────────────────────────────────


async def _process_upload(
    file: UploadFile, max_mb: int
) -> tuple[bytes, str, list[dict]]:
    """
    Yüklenen dosyayı doğrular ve işler.
    Döner: (ham_içerik, sha256_hash, sayfa_listesi)
    Hata durumunda HTTPException fırlatır.
    """
    # 1. Uzantı kontrolü — içerik okunmadan önce yapılır
    filename = file.filename or ""
    if not filename.lower().endswith(_ALLOWED_EXTENSION):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Yalnızca PDF dosyaları (.pdf) kabul edilmektedir.",
        )

    # 2. İçeriği oku (limit + 1 byte: taşma tespiti için)
    content = await file.read(max_mb * 1024 * 1024 + 1)

    # 3. Boyut kontrolü
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Dosya {max_mb} MB sınırını aşıyor.",
        )

    # 4. Magic byte kontrolü (content-type başlığına güvenilmez)
    if not is_valid_pdf_content(content):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Dosya geçerli bir PDF gibi görünmüyor (magic byte uyuşmazlığı).",
        )

    # 5. Hash ve metin çıkarma
    sha256 = calculate_sha256(content)
    try:
        pages_data = extract_pages(content)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    return content, sha256, pages_data


async def _build_document_response(
    db: AsyncSession, document: Document
) -> DocumentResponse:
    """Güncel versiyonu veritabanından alır ve DocumentResponse oluşturur."""
    result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_number == document.current_version_number,
        )
    )
    current_version = result.scalar_one_or_none()

    return DocumentResponse(
        id=document.id,
        asset_id=document.asset_id,
        document_type=document.document_type,
        title=document.title,
        status=document.status,
        current_version_number=document.current_version_number,
        created_at=document.created_at,
        updated_at=document.updated_at,
        current_version=(
            DocumentVersionResponse.model_validate(current_version)
            if current_version
            else None
        ),
    )


async def _save_version_records(
    db: AsyncSession,
    storage: StorageService,
    document_id: str,
    version_number: int,
    file: UploadFile,
    content: bytes,
    sha256: str,
    pages_data: list[dict],
) -> DocumentVersion:
    """DocumentVersion + DocumentPage kayıtlarını oluşturur; dosyayı diske yazar."""
    safe_filename = (file.filename or "document.pdf").replace(" ", "_")
    stored_path = storage.save(document_id, version_number, safe_filename, content)

    version_id = str(uuid.uuid4())
    version = DocumentVersion(
        id=version_id,
        document_id=document_id,
        version_number=version_number,
        original_filename=file.filename or "document.pdf",
        stored_path=stored_path,
        file_size=len(content),
        sha256_hash=sha256,
    )
    db.add(version)
    await db.flush()  # version_id'yi FK için DB'ye yaz

    for page in pages_data:
        db.add(
            DocumentPage(
                id=str(uuid.uuid4()),
                version_id=version_id,
                page_number=page["page_number"],
                text_content=page["text_content"],
            )
        )
    return version


# ── Endpointler ───────────────────────────────────────────────────────────────


@router.post(
    "/assets/{asset_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Varlığa yeni yasal belge yükle",
)
async def upload_document(
    asset_id: str,
    file: UploadFile,
    document_type: DocumentType = Form(...),
    title: str = Form(..., min_length=1, max_length=255),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> DocumentResponse:
    # Varlık kontrolü
    asset_result = await db.execute(select(Asset).where(Asset.id == asset_id))
    if asset_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Varlık bulunamadı.")

    content, sha256, pages_data = await _process_upload(file, settings.max_upload_size_mb)

    # Belge kaydını oluştur
    doc_id = str(uuid.uuid4())
    document = Document(
        id=doc_id,
        asset_id=asset_id,
        document_type=document_type,
        title=title,
        status="active",
        current_version_number=1,
    )
    db.add(document)
    await db.flush()

    await _save_version_records(db, storage, doc_id, 1, file, content, sha256, pages_data)
    await db.commit()
    await db.refresh(document)

    return await _build_document_response(db, document)


@router.get(
    "/assets/{asset_id}/documents",
    response_model=list[DocumentResponse],
    summary="Varlığa ait tüm belgeleri listele",
)
async def list_documents(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    asset_result = await db.execute(select(Asset).where(Asset.id == asset_id))
    if asset_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Varlık bulunamadı.")

    result = await db.execute(
        select(Document).where(Document.asset_id == asset_id).order_by(Document.created_at.desc())
    )
    documents = result.scalars().all()
    return [await _build_document_response(db, doc) for doc in documents]


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    summary="Belge meta verilerini getir",
)
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belge bulunamadı.")
    return await _build_document_response(db, document)


@router.post(
    "/documents/{document_id}/versions",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Mevcut belgeye yeni versiyon yükle",
)
async def upload_new_version(
    document_id: str,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> DocumentVersionResponse:
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belge bulunamadı.")

    content, sha256, pages_data = await _process_upload(file, settings.max_upload_size_mb)

    new_version_number = document.current_version_number + 1
    version = await _save_version_records(
        db, storage, document_id, new_version_number, file, content, sha256, pages_data
    )

    document.current_version_number = new_version_number
    document.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(version)
    return version


@router.get(
    "/documents/{document_id}/versions",
    response_model=list[DocumentVersionResponse],
    summary="Belgenin tüm versiyonlarını listele",
)
async def list_versions(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[DocumentVersionResponse]:
    doc_result = await db.execute(select(Document).where(Document.id == document_id))
    if doc_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belge bulunamadı.")

    result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number)
    )
    return result.scalars().all()


@router.get(
    "/documents/{document_id}/text",
    response_model=DocumentTextResponse,
    summary="Belgenin güncel versiyonundan çıkarılan metni getir",
)
async def get_document_text(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> DocumentTextResponse:
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belge bulunamadı.")

    version_result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == document.current_version_number,
        )
    )
    current_version = version_result.scalar_one_or_none()
    if current_version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Versiyon bulunamadı.")

    pages_result = await db.execute(
        select(DocumentPage)
        .where(DocumentPage.version_id == current_version.id)
        .order_by(DocumentPage.page_number)
    )
    pages = pages_result.scalars().all()

    return DocumentTextResponse(
        document_id=document_id,
        version_number=current_version.version_number,
        pages=[DocumentPageResponse.model_validate(p) for p in pages],
    )


@router.get(
    "/documents/{document_id}/download",
    summary="Belgenin güncel versiyonunu indir (PDF)",
)
async def download_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> FileResponse:
    result = await db.execute(select(Document).where(Document.id == document_id))
    document = result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belge bulunamadı.")

    version_result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == document.current_version_number,
        )
    )
    current_version = version_result.scalar_one_or_none()
    if current_version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Versiyon bulunamadı.")

    full_path = storage.full_path(current_version.stored_path)
    if not full_path.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Dosya diskte bulunamadı.",
        )

    return FileResponse(
        path=str(full_path),
        media_type="application/pdf",
        filename=current_version.original_filename,
    )
