"""
Dosya depolama servisi.

Her belge versiyonu şu yapıda saklanır:
  <storage_root>/<document_id>/v<version_number>/<filename>

stored_path: storage_root'a göre göreli yol (POSIX formatında, / ile ayrılmış)
Bu sayede farklı işletim sistemlerinde veritabanı kaydı tutarlı kalır.
"""

from pathlib import Path

from app.config import settings


class StorageService:
    def __init__(self, base_path: str) -> None:
        self.base_path = Path(base_path)

    def save(self, document_id: str, version_number: int, filename: str, data: bytes) -> str:
        """
        Dosyayı diske yazar ve storage_root'a göre göreli POSIX yolunu döner.
        Klasörler yoksa otomatik oluşturulur.
        """
        version_dir = self.base_path / document_id / f"v{version_number}"
        version_dir.mkdir(parents=True, exist_ok=True)
        file_path = version_dir / filename
        file_path.write_bytes(data)
        return file_path.relative_to(self.base_path).as_posix()

    def load(self, stored_path: str) -> bytes:
        """Göreli yoldan dosyayı okur."""
        return (self.base_path / Path(stored_path)).read_bytes()

    def full_path(self, stored_path: str) -> Path:
        """FileResponse için mutlak Path döner."""
        return self.base_path / Path(stored_path)

    def exists(self, stored_path: str) -> bool:
        return self.full_path(stored_path).exists()


def get_storage() -> StorageService:
    """FastAPI dependency: varsayılan storage servisi."""
    return StorageService(settings.storage_path)
