"""
File storage service.

Every document version is stored with this layout:
  <storage_root>/<document_id>/v<version_number>/<random-uuid>.pdf

SECURITY (Stage 9B-2):
- The filename sent by the user is never used as a path; it is stored only as
  metadata. The filename is a random UUID generated on the server.
- document_id must be a UUID and version_number a positive integer.
- Every path is resolved (following symlinks / junctions) and verified to stay
  inside the storage root; a path that escapes is rejected.
- Files are created with O_EXCL: no existing file is ever overwritten.
- The written bytes are read back from disk and verified with SHA-256.

stored_path: POSIX path relative to the storage root (stored in the database,
never shown in API responses).
"""

import hashlib
import logging
import os
import re
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.config import settings

security_logger = logging.getLogger("rwa_security")

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_STORED_PATH_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/v[1-9][0-9]{0,8}/[0-9a-f]{32}\.pdf$"
)
# O_BINARY exists only on Windows; O_NOFOLLOW only on POSIX
_OPEN_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)


class StorageError(Exception):
    """Unsafe path, write error or missing file. The message contains no internal path."""


class StorageIntegrityError(StorageError):
    """The SHA-256 digest of the file on disk does not match the recorded digest."""


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class StorageService:
    def __init__(self, base_path: str) -> None:
        base = Path(base_path)
        base.mkdir(parents=True, exist_ok=True)
        # The root is resolved once; every target is checked against this absolute root
        self.base_path = base.resolve(strict=True)

    # ── Path safety ──────────────────────────────────────────────────────────

    def _confine(self, path: Path) -> Path:
        """Resolves the path (including symlinks/junctions) and verifies it is inside the root."""
        resolved = path.resolve(strict=False)
        if resolved != self.base_path and not resolved.is_relative_to(self.base_path):
            security_logger.warning("storage.path_escape_blocked")
            raise StorageError("Unsafe storage path.")
        return resolved

    @staticmethod
    def _validate_stored_path(stored_path: str) -> PurePosixPath:
        """Validates the format of a path coming from the database (defense in depth)."""
        if (
            not isinstance(stored_path, str)
            or "\\" in stored_path
            or "\x00" in stored_path
            or PureWindowsPath(stored_path).drive
            or PurePosixPath(stored_path).is_absolute()
            or ".." in PurePosixPath(stored_path).parts
            or not _STORED_PATH_RE.match(stored_path)
        ):
            security_logger.warning("storage.invalid_stored_path")
            raise StorageError("Unsafe storage path.")
        return PurePosixPath(stored_path)

    def full_path(self, stored_path: str) -> Path:
        """Returns the absolute path for a stored relative path, verified to stay inside the root."""
        rel = self._validate_stored_path(stored_path)
        return self._confine(self.base_path.joinpath(*rel.parts))

    # ── Writing ──────────────────────────────────────────────────────────────

    def build_stored_path(self, document_id: str, version_number: int) -> str:
        """Unique, server-generated relative path for a new version."""
        if not isinstance(document_id, str) or not _UUID_RE.match(document_id):
            raise StorageError("Invalid document identifier.")
        if not isinstance(version_number, int) or isinstance(version_number, bool) or version_number < 1:
            raise StorageError("Invalid version number.")
        return f"{document_id}/v{version_number}/{uuid.uuid4().hex}.pdf"

    def save_new(self, stored_path: str, data: bytes, expected_sha256: str) -> None:
        """
        Creates the file only if it does not exist yet (O_EXCL), reads the bytes
        written to disk back and verifies them with SHA-256. On error the partial file is deleted.
        """
        target = self.full_path(stored_path)
        parent = target.parent
        parent.mkdir(parents=True, exist_ok=True)
        # Re-check after mkdir: a link escaping the root may have been placed instead of an intermediate folder
        self._confine(parent)
        try:
            fd = os.open(target, _OPEN_FLAGS, 0o600)
        except FileExistsError:
            security_logger.warning("storage.overwrite_blocked")
            raise StorageError("Target file already exists; it will not be overwritten.") from None
        except OSError as exc:
            raise StorageError(f"File could not be created ({type(exc).__name__}).") from None
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            if sha256_hex(target.read_bytes()) != expected_sha256:
                raise StorageIntegrityError("Written file failed verification.")
        except BaseException:
            self.discard(stored_path)
            raise

    def discard(self, stored_path: str) -> None:
        """Deletes the file created by this request (cleanup after a failed record)."""
        try:
            self.full_path(stored_path).unlink(missing_ok=True)
        except (StorageError, OSError):
            security_logger.error("storage.cleanup_failed")

    # ── Reading ──────────────────────────────────────────────────────────────

    def read_verified(self, stored_path: str, expected_sha256: str) -> bytes:
        """Reads the file and verifies that it matches the recorded SHA-256."""
        path = self.full_path(stored_path)
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise StorageError("Stored file not found.") from None
        except OSError as exc:
            raise StorageError(f"File could not be read ({type(exc).__name__}).") from None
        if sha256_hex(data) != expected_sha256:
            security_logger.error("storage.integrity_mismatch stored_path_sha=%s", sha256_hex(stored_path.encode())[:12])
            raise StorageIntegrityError("Document integrity check failed.")
        return data

    def exists(self, stored_path: str) -> bool:
        try:
            return self.full_path(stored_path).is_file()
        except StorageError:
            return False


def get_storage() -> StorageService:
    """FastAPI dependency: default storage service."""
    return StorageService(settings.storage_path)
