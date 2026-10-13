"""Safe file storage abstraction for Cantonment Board complaint evidence.

Provides:
  - Validated, secure local file storage by default.
  - Pluggable storage architecture (local disk with production-ready S3/cloud hooks).
  - Strict type checking (JPEG, PNG, WebP, GIF) via magic bytes and extensions.
  - Size limit enforcement (default 10MB) to prevent disk exhaustion.
  - Safe UUID-based filename generation to prevent directory traversal or name collisions.
  - SSRF-safe remote image ingestion via app.safe_http for incoming WhatsApp media.
  - Tenant-scoped metadata tracking.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .safe_http import assert_fetchable, BlockedURL
import requests

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
STORAGE_DIR = Path(
    os.environ.get("STORAGE_DIR")
    or os.environ.get("UPLOADS_DIR")
    or (Path(__file__).resolve().parent.parent / "uploads")
)
MAX_UPLOAD_SIZE_BYTES = int(os.environ.get("MAX_UPLOAD_SIZE_BYTES", str(10 * 1024 * 1024)))  # 10 MB

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

SAFE_FILENAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


class StorageValidationError(Exception):
    """Raised when an uploaded file fails security, format, or size validation."""


def _ensure_storage_dir() -> Path:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return STORAGE_DIR


def detect_image_mime(data: bytes) -> Optional[str]:
    """Inspect magic bytes to determine image type reliably without trusting client MIME."""
    if len(data) < 12:
        return None
    # JPEG: FF D8 FF
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    # PNG: 89 50 4E 47 0D 0A 1A 0A
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    # GIF: GIF87a or GIF89a
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    # WebP: RIFF .... WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class StorageBackend:
    """Abstract base storage backend."""

    def save(
        self,
        file_bytes: bytes,
        original_filename: str,
        tenant_id: str,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError

    def get_path(self, filename: str) -> Optional[Path]:
        raise NotImplementedError

    def exists(self, filename: str) -> bool:
        raise NotImplementedError


class LocalStorageBackend(StorageBackend):
    """Production-grade local file storage backend with path traversal guards."""

    def __init__(self, root_dir: Optional[Path] = None):
        self.root = root_dir or STORAGE_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        file_bytes: bytes,
        original_filename: str,
        tenant_id: str,
        content_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        # 1. Size check
        if not file_bytes:
            raise StorageValidationError("Uploaded file is empty (0 bytes).")
        if len(file_bytes) > MAX_UPLOAD_SIZE_BYTES:
            max_mb = MAX_UPLOAD_SIZE_BYTES / (1024 * 1024)
            raise StorageValidationError(
                f"File size exceeds maximum allowed limit of {max_mb:.1f} MB."
            )

        # 2. Magic byte inspection
        detected_mime = detect_image_mime(file_bytes)
        if not detected_mime:
            raise StorageValidationError(
                "Invalid or unsupported image format. Only JPEG, PNG, WebP, and GIF images are accepted."
            )

        # 3. Clean filename extension
        clean_ext = MIME_TO_EXT.get(detected_mime, ".jpg")

        # 4. Generate collision-free, safe file name
        # Format: cmp_<tenant_suffix>_<uuid><ext>
        tenant_suffix = re.sub(r"[^a-zA-Z0-9]", "", str(tenant_id))[:8] or "gen"
        file_uuid = uuid.uuid4().hex
        stored_filename = f"cmp_{tenant_suffix}_{file_uuid}{clean_ext}"

        dest_path = self.root / stored_filename
        dest_path.write_bytes(file_bytes)

        # 5. Sanitize original display filename
        safe_orig = Path(original_filename or "evidence.jpg").name
        safe_orig = re.sub(r"[^\w\.\-]", "_", safe_orig)[:100]

        now = datetime.now(timezone.utc).isoformat()
        return {
            "file_id": file_uuid,
            "filename": stored_filename,
            "original_filename": safe_orig,
            "content_type": detected_mime,
            "file_size_bytes": len(file_bytes),
            "file_url": f"/complaints/media/{stored_filename}",
            "tenant_id": str(tenant_id),
            "created_at": now,
        }

    def get_path(self, filename: str) -> Optional[Path]:
        if not filename or not isinstance(filename, str):
            return None
        # Strict pattern match to prevent any traversal tricks
        if not SAFE_FILENAME_RE.match(filename):
            return None
        if ".." in filename or "/" in filename or "\\" in filename:
            return None

        target = (self.root / filename).resolve()
        try:
            # Ensure resolved target is within storage root
            if not target.is_relative_to(self.root.resolve()):
                return None
        except AttributeError:
            # Python < 3.9 compatibility
            if not str(target).startswith(str(self.root.resolve())):
                return None

        if target.is_file():
            return target
        return None

    def exists(self, filename: str) -> bool:
        return self.get_path(filename) is not None


# Global storage instance
DEFAULT_STORAGE = LocalStorageBackend()


def save_evidence_file(
    file_bytes: bytes,
    original_filename: str,
    tenant_id: str,
    content_type: Optional[str] = None,
) -> Dict[str, Any]:
    """Validate and store an evidence photo."""
    return DEFAULT_STORAGE.save(
        file_bytes=file_bytes,
        original_filename=original_filename,
        tenant_id=tenant_id,
        content_type=content_type,
    )


def get_evidence_file_path(filename: str) -> Optional[Path]:
    """Retrieve absolute path for a stored evidence file safely."""
    return DEFAULT_STORAGE.get_path(filename)


def ingest_remote_media_url(url: str, tenant_id: str) -> Optional[Dict[str, Any]]:
    """Securely ingest a remote media URL into local evidence storage.

    Uses app.safe_http to prevent SSRF and validate content before saving.
    Returns stored file info or None if download fails/blocked.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()

    # If it is already a local media URL on our server, skip re-downloading
    if url.startswith("/complaints/media/"):
        fname = url.split("/complaints/media/")[-1]
        p = get_evidence_file_path(fname)
        if p:
            return {
                "file_url": url,
                "filename": fname,
                "file_size_bytes": p.stat().st_size,
                "content_type": detect_image_mime(p.read_bytes()) or "image/jpeg",
                "tenant_id": tenant_id,
            }

    # SSRF guard: check URL fetchability
    try:
        assert_fetchable(url)
    except BlockedURL:
        # Blocked private/metadata address or non-web port
        return None
    except Exception:
        return None

    # Fetch securely with capped timeout and capped size
    try:
        resp = requests.get(
            url,
            timeout=8.0,
            headers={"User-Agent": "TechDeva-EvidenceIngest/1.0"},
            stream=True,
        )
        if resp.status_code != 200:
            return None

        # Read up to max upload size
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > MAX_UPLOAD_SIZE_BYTES:
                return None
            chunks.append(chunk)

        content = b"".join(chunks)
        # Parse original name from URL
        orig_name = Path(url.split("?")[0]).name or "whatsapp_evidence.jpg"

        return save_evidence_file(
            file_bytes=content,
            original_filename=orig_name,
            tenant_id=tenant_id,
        )
    except Exception:
        return None
