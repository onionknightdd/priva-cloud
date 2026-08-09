"""Temporary file management for file uploads."""

from __future__ import annotations

import fcntl
import json
import mimetypes
import os
import secrets
import shutil
import threading
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from priva_common.logging import get_app_logger
from priva_common.config import get_settings

logger = get_app_logger(__name__)

MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB — keep in sync with PromptComposer.jsx
TTL_SECONDS = 24 * 60 * 60  # 24 hours
METADATA_FILENAME = "metadata.json"
MAX_SAFE_FILENAME_BYTES = 200
ATTACHMENT_ID_BYTES = 9
ATTACHMENT_ID_LENGTH = 12
_STAGING_SUFFIX = ".uploading"
_BASE64URL_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)

ALLOWED_EXTENSIONS = {
    ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".pdf", ".zip",
    ".txt", ".csv", ".json", ".xml", ".md", ".log",
    ".yaml", ".yml", ".toml", ".ini", ".conf",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".html", ".css", ".sh", ".sql",
    ".r", ".lua", ".swift", ".kt", ".scala",
    ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp",
    ".env", ".dockerfile",
    ".png", ".jpg", ".jpeg", ".gif", ".webp",
}

ZIP_SIGNATURES = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
)
OLE_SIGNATURE = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"
PDF_SIGNATURE = b"%PDF-"

_TEXT_EXTENSIONS = frozenset({
    ".txt", ".csv", ".json", ".xml", ".md", ".log",
    ".yaml", ".yml", ".toml", ".ini", ".conf",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".html", ".css", ".sh", ".sql",
    ".r", ".lua", ".swift", ".kt", ".scala",
    ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".cpp", ".h", ".hpp",
    ".env", ".dockerfile",
})

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

_index_lock = threading.Lock()


def get_temp_dir(username: str) -> Path:
    """Return base file directory: {workspace}/{username}/temp/uploads/"""
    settings = get_settings()
    base = os.path.expanduser(settings.server.work_dir)
    return Path(base) / username / "temp" / "uploads"


def _get_date_dir(username: str, date_str: str | None = None) -> Path:
    """Return date subdirectory: {workspace}/{username}/temp/uploads/{YYYY-MM-DD}/"""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return get_temp_dir(username) / date_str


def _get_index_path(username: str) -> Path:
    """Return index file path: {workspace}/{username}/temp/uploads/.index.jsonl"""
    return get_temp_dir(username) / ".index.jsonl"


def _safe_resolve(base: Path, relative: str) -> Path:
    """Resolve path and verify it's inside base directory."""
    resolved = (base / relative).resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "Path traversal detected")
    return resolved


def _filename_extension(filename: str) -> str:
    """Return the validated extension, including supported dotfile names."""
    basename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    lowered = basename.lower()
    if lowered in {".env", ".dockerfile"}:
        return lowered
    return Path(basename).suffix.lower()


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def sanitize_upload_filename(filename: str) -> str:
    """Return a safe, readable basename for storage inside an ID directory."""
    normalized = unicodedata.normalize("NFC", filename or "upload")
    basename = normalized.replace("\\", "/").rsplit("/", 1)[-1]
    basename = "".join(
        "_" if unicodedata.category(char).startswith("C") else char
        for char in basename
    ).strip().rstrip(" .")
    if basename in {"", ".", ".."}:
        basename = "upload"
    if basename == METADATA_FILENAME:
        basename = f"attachment-{basename}"

    suffix = Path(basename).suffix
    suffix_bytes = len(suffix.encode("utf-8"))
    if suffix and suffix_bytes < MAX_SAFE_FILENAME_BYTES:
        stem = basename[:-len(suffix)]
        stem = _truncate_utf8(stem, MAX_SAFE_FILENAME_BYTES - suffix_bytes)
        basename = f"{stem or 'upload'}{suffix}"
    else:
        basename = _truncate_utf8(basename, MAX_SAFE_FILENAME_BYTES) or "upload"
    return basename


def validate_file(filename: str, size: int) -> None:
    """Validate file extension and size."""
    ext = _filename_extension(filename)
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(400, f"Unsupported file type: {ext}")
    if size > MAX_FILE_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_SIZE // (1024 * 1024)}MB size limit")


def _is_zip_container(data: bytes) -> bool:
    return any(data.startswith(signature) for signature in ZIP_SIGNATURES)


def _is_ole_container(data: bytes) -> bool:
    return data.startswith(OLE_SIGNATURE)


def _is_pdf(data: bytes) -> bool:
    return data.startswith(PDF_SIGNATURE)


def _looks_like_text(data: bytes) -> bool:
    """Heuristic: return True if `data` looks like a text/source file.

    Empty bytes are treated as text. Otherwise we inspect the first 8KB,
    reject any NUL byte (a strong binary signal), then try UTF-8. UTF-8
    failures fall back to Latin-1 with a control-character ratio check so
    legacy encodings still pass without letting random binary slip through.
    """
    if not data:
        return True
    window = data[:8192]
    if b"\x00" in window:
        return False
    if window.startswith(b"\xef\xbb\xbf"):
        window = window[3:]
    try:
        window.decode("utf-8")
        return True
    except UnicodeDecodeError:
        pass
    try:
        text = window.decode("latin-1")
    except UnicodeDecodeError:
        return False
    if not text:
        return True
    control = 0
    for ch in text:
        code = ord(ch)
        if code <= 0x08 or code == 0x0B or code == 0x0C or (0x0E <= code <= 0x1F) or code == 0x7F:
            control += 1
    return (control / len(text)) < 0.05


def validate_file_content(filename: str, data: bytes) -> None:
    """Validate office file container signatures to catch mislabeled binary files."""
    ext = _filename_extension(filename)

    if ext in {".xlsx", ".docx", ".pptx"} and not _is_zip_container(data):
        raise HTTPException(400, f"Invalid {ext} file: expected a ZIP-based Office document")

    if ext in {".xls", ".doc", ".ppt"} and not (_is_zip_container(data) or _is_ole_container(data)):
        raise HTTPException(400, f"Invalid {ext} file: expected an OLE or ZIP-based Office document")

    if ext == ".pdf" and not _is_pdf(data):
        raise HTTPException(400, "Invalid .pdf file: expected '%PDF-' header")

    if ext == ".zip" and not _is_zip_container(data):
        raise HTTPException(400, "Invalid .zip file: expected ZIP signature")

    if ext in _IMAGE_EXTENSIONS:
        valid = (
            (ext == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
            or (ext in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"))
            or (ext == ".gif" and data.startswith((b"GIF87a", b"GIF89a")))
            or (
                ext == ".webp"
                and len(data) >= 12
                and data.startswith(b"RIFF")
                and data[8:12] == b"WEBP"
            )
        )
        if not valid:
            raise HTTPException(400, f"Invalid {ext} image: file signature does not match")

    if ext in _TEXT_EXTENSIONS and not _looks_like_text(data):
        raise HTTPException(400, "Invalid text file: contains binary data")


def _read_index(username: str) -> list[dict]:
    """Read all entries from .index.jsonl, auto-clean entries whose files are missing."""
    index_path = _get_index_path(username)
    if not index_path.exists():
        return []

    entries = []
    dirty = False
    with _index_lock:
        with open(index_path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        dirty = True
                        continue
                    if entry.get("deleted"):
                        dirty = True
                        continue
                    # Verify file exists on disk
                    file_path = _get_date_dir(username, entry["upload_date"]) / entry["stored_name"]
                    if not file_path.is_file():
                        dirty = True
                        continue
                    entries.append(entry)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # Rewrite index if we found stale entries
    if dirty:
        _rewrite_index(username, entries)

    return entries


def _append_index_entry(username: str, entry: dict) -> None:
    """Append one JSONL line to .index.jsonl with exclusive lock."""
    index_path = _get_index_path(username)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with _index_lock:
        with open(index_path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, default=str) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def _remove_index_entry(username: str, attachment_id: str) -> dict | None:
    """Remove a legacy index entry by attachment ID."""
    index_path = _get_index_path(username)
    if not index_path.exists():
        return None

    removed = None
    remaining = []
    with _index_lock:
        with open(index_path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    entry_id = entry.get("attachment_id") or entry.get("uuid")
                    if entry_id == attachment_id:
                        removed = entry
                    else:
                        remaining.append(entry)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        if removed is not None:
            with open(index_path, "w") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    for entry in remaining:
                        f.write(json.dumps(entry, default=str) + "\n")
                finally:
                    fcntl.flock(f, fcntl.LOCK_UN)

    return removed


def _rewrite_index(username: str, entries: list[dict]) -> None:
    """Rewrite .index.jsonl with given entries (exclusive lock)."""
    index_path = _get_index_path(username)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with _index_lock:
        with open(index_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                for entry in entries:
                    f.write(json.dumps(entry, default=str) + "\n")
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


def _is_legacy_uuid_value(value: str) -> bool:
    return bool(
        value
        and len(value) == 32
        and all(char in "0123456789abcdef" for char in value.lower())
    )


def _is_attachment_id_value(value: str) -> bool:
    """Accept current 12-char IDs and legacy 32-char hexadecimal UUIDs."""
    return bool(
        isinstance(value, str)
        and (
            (len(value) == ATTACHMENT_ID_LENGTH and all(c in _BASE64URL_CHARS for c in value))
            or _is_legacy_uuid_value(value)
        )
    )


def _new_attachment_id() -> str:
    attachment_id = secrets.token_urlsafe(ATTACHMENT_ID_BYTES)
    if len(attachment_id) != ATTACHMENT_ID_LENGTH:  # defensive: 9 bytes always encode to 12 chars
        raise RuntimeError("Unexpected attachment ID length")
    return attachment_id


def _attachment_dir(username: str, attachment_id: str) -> Path:
    return get_temp_dir(username) / attachment_id


def _load_attachment_entry(username: str, attachment_id: str) -> dict | None:
    """Load one ID-directory attachment, returning API-compatible metadata."""
    attachment_dir = _attachment_dir(username, attachment_id)
    if not attachment_dir.is_dir() or attachment_dir.is_symlink():
        return None
    metadata_path = attachment_dir / METADATA_FILENAME
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        logger.warning("Ignoring invalid attachment metadata at {}", metadata_path)
        return None
    if not isinstance(metadata, dict):
        logger.warning("Ignoring non-object attachment metadata at {}", metadata_path)
        return None
    metadata_id = metadata.get("attachment_id") or metadata.get("uuid")
    if metadata_id != attachment_id:
        logger.warning("Ignoring attachment metadata with mismatched ID at {}", metadata_path)
        return None

    safe_name = metadata.get("safe_name")
    if not isinstance(safe_name, str) or not safe_name:
        return None
    try:
        file_path = _safe_resolve(attachment_dir, safe_name)
    except HTTPException:
        logger.warning("Ignoring unsafe attachment payload path at {}", metadata_path)
        return None
    if file_path.parent != attachment_dir.resolve() or not file_path.is_file():
        return None

    entry = dict(metadata)
    entry.setdefault("attachment_id", attachment_id)
    # ``uuid`` and ``stored_name`` remain during the API compatibility window.
    entry.setdefault("uuid", attachment_id)
    entry.setdefault("stored_name", safe_name)
    entry["path"] = str(file_path)
    return entry


def _iter_attachment_entries(username: str) -> list[dict]:
    base = get_temp_dir(username)
    if not base.is_dir():
        return []
    entries = []
    for child in base.iterdir():
        if not child.is_dir() or not _is_attachment_id_value(child.name):
            continue
        entry = _load_attachment_entry(username, child.name)
        if entry is not None:
            entries.append(entry)
    return entries


def _legacy_entry_with_path(username: str, entry: dict) -> dict:
    item = dict(entry)
    item.setdefault("attachment_id", item.get("uuid"))
    item["path"] = str(
        _get_date_dir(username, item["upload_date"]) / item["stored_name"]
    )
    return item


def save_temp_file(username: str, filename: str, data: bytes) -> tuple[str, str, str, int]:
    """Atomically save ``uploads/{attachment_id}/{safe_original_name}`` + metadata.

    Returns ``(attachment_id, safe_name, full_path, size)``. The ID directory is
    published with one atomic rename so list/download never observe a partial
    upload containing only the payload or only its metadata.
    """
    original_name = filename or "upload"
    safe_name = sanitize_upload_filename(original_name)
    ext = _filename_extension(original_name)
    base = get_temp_dir(username)
    base.mkdir(parents=True, exist_ok=True)

    while True:
        attachment_id = _new_attachment_id()
        attachment_dir = base / attachment_id
        staging_dir = base / f".{attachment_id}{_STAGING_SUFFIX}"
        if not attachment_dir.exists() and not staging_dir.exists():
            break

    uploaded_at = datetime.now(timezone.utc)
    metadata = {
        "attachment_id": attachment_id,
        "original_name": original_name,
        "safe_name": safe_name,
        "ext": ext,
        "size": len(data),
        "mime_type": mimetypes.guess_type(original_name)[0] or "application/octet-stream",
        "upload_date": uploaded_at.strftime("%Y-%m-%d"),
        "uploaded_at": uploaded_at.isoformat(),
    }

    try:
        staging_dir.mkdir(mode=0o700)
        (staging_dir / safe_name).write_bytes(data)
        (staging_dir / METADATA_FILENAME).write_text(
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(staging_dir, attachment_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    return attachment_id, safe_name, str((attachment_dir / safe_name).resolve()), len(data)


def _validate_attachment_id(attachment_id: str) -> None:
    """Validate a current Base64URL ID or a legacy hexadecimal UUID."""
    if not _is_attachment_id_value(attachment_id):
        raise HTTPException(400, "Invalid attachment ID")


def _validate_uuid(file_uuid: str) -> None:
    """Compatibility alias for callers using the previous helper name."""
    _validate_attachment_id(file_uuid)


def delete_temp_file(username: str, attachment_id: str) -> None:
    """Delete an ID-directory attachment, with legacy-index fallback."""
    _validate_attachment_id(attachment_id)

    attachment_dir = _attachment_dir(username, attachment_id)
    if attachment_dir.is_dir() and not attachment_dir.is_symlink():
        shutil.rmtree(attachment_dir)
        return

    # Compatibility for uploads created by the previous date/index layout.
    removed = _remove_index_entry(username, attachment_id)
    if removed is None:
        raise HTTPException(404, "File not found")
    file_path = _get_date_dir(username, removed["upload_date"]) / removed["stored_name"]
    try:
        file_path.unlink()
    except FileNotFoundError:
        pass


def list_temp_files(username: str, date_filter: str | None = None) -> list[dict]:
    """List ID-directory attachments plus legacy indexed uploads."""
    entries = _iter_attachment_entries(username)
    known_ids = {entry.get("attachment_id") or entry.get("uuid") for entry in entries}
    entries.extend(
        _legacy_entry_with_path(username, entry)
        for entry in _read_index(username)
        if (entry.get("attachment_id") or entry.get("uuid")) not in known_ids
    )
    if date_filter:
        entries = [e for e in entries if e.get("upload_date") == date_filter]
    return sorted(entries, key=lambda entry: entry.get("uploaded_at") or "")


def get_file_by_id(username: str, attachment_id: str) -> tuple[str, str, str]:
    """Look up file by attachment ID. Returns (path, original name, MIME type).

    Raises 404 if not found or file missing on disk.
    """
    _validate_attachment_id(attachment_id)
    entry = _load_attachment_entry(username, attachment_id)
    if entry is not None:
        return (
            entry["path"],
            entry["original_name"],
            entry.get("mime_type", "application/octet-stream"),
        )

    # Compatibility for uploads created by the previous date/index layout.
    entry = next(
        (
            e
            for e in _read_index(username)
            if (e.get("attachment_id") or e.get("uuid")) == attachment_id
        ),
        None,
    )
    if entry is None:
        raise HTTPException(404, "File not found")

    file_path = _get_date_dir(username, entry["upload_date"]) / entry["stored_name"]

    # Verify within user's temp directory
    base = get_temp_dir(username)
    resolved = file_path.resolve()
    try:
        resolved.relative_to(base.resolve())
    except ValueError:
        raise HTTPException(400, "Path traversal detected")

    if not file_path.is_file():
        # Auto-clean dangling entry
        _remove_index_entry(username, attachment_id)
        raise HTTPException(404, "File not found")

    return str(file_path), entry["original_name"], entry.get("mime_type", "application/octet-stream")


def get_file_by_uuid(username: str, file_uuid: str) -> tuple[str, str, str]:
    """Compatibility alias for the previous public helper name."""
    return get_file_by_id(username, file_uuid)


def cleanup_expired_files() -> int:
    """Delete expired temp files across all users. Returns count deleted."""
    settings = get_settings()
    base = Path(os.path.expanduser(settings.server.work_dir))
    if not base.exists():
        return 0

    now = time.time()
    deleted = 0

    for user_dir in base.iterdir():
        if not user_dir.is_dir():
            continue

        username = user_dir.name

        # Clean ID-directory uploads and compatibility date/index uploads.
        file_dir = user_dir / "temp" / "uploads"
        if file_dir.is_dir():
            for sub in list(file_dir.iterdir()):
                if not sub.is_dir():
                    continue
                is_attachment = _is_attachment_id_value(sub.name)
                staging_id = (
                    sub.name[1:-len(_STAGING_SUFFIX)]
                    if sub.name.startswith(".") and sub.name.endswith(_STAGING_SUFFIX)
                    else ""
                )
                is_staging = _is_attachment_id_value(staging_id)
                if not is_attachment and not is_staging:
                    continue
                try:
                    if now - sub.stat().st_mtime > TTL_SECONDS:
                        shutil.rmtree(sub)
                        deleted += 1
                except FileNotFoundError:
                    pass

            index_path = file_dir / ".index.jsonl"
            if index_path.exists():
                # Legacy index entries remain readable until their TTL expires.
                entries = _read_index(username)
                kept = []
                for entry in entries:
                    fp = _get_date_dir(username, entry["upload_date"]) / entry["stored_name"]
                    try:
                        if now - fp.stat().st_mtime > TTL_SECONDS:
                            try:
                                fp.unlink()
                            except FileNotFoundError:
                                pass
                            deleted += 1
                        else:
                            kept.append(entry)
                    except FileNotFoundError:
                        deleted += 1
                if len(kept) != len(entries):
                    _rewrite_index(username, kept)

            # Scan legacy date directories for orphaned files.
            indexed_files: set[str] = set()
            if index_path.exists():
                for entry in _read_index(username):
                    fp = _get_date_dir(username, entry["upload_date"]) / entry["stored_name"]
                    indexed_files.add(str(fp.resolve()))

            for sub in file_dir.iterdir():
                if (
                    not sub.is_dir()
                    or sub.name.startswith(".")
                    or _is_attachment_id_value(sub.name)
                ):
                    continue
                for f in sub.iterdir():
                    if not f.is_file():
                        continue
                    if str(f.resolve()) not in indexed_files:
                        try:
                            if now - f.stat().st_mtime > TTL_SECONDS:
                                f.unlink()
                                deleted += 1
                        except Exception:
                            pass
                # Remove empty date directories
                try:
                    if sub.is_dir() and not any(sub.iterdir()):
                        sub.rmdir()
                except Exception:
                    pass

        # Clean the older flat temp/ directory (pre-date-partitioned layout)
        legacy_temp = user_dir / "temp"
        if legacy_temp.is_dir():
            for f in legacy_temp.iterdir():
                if not f.is_file():
                    continue
                try:
                    if now - f.stat().st_mtime > TTL_SECONDS:
                        f.unlink()
                        deleted += 1
                except Exception:
                    pass

    if deleted:
        logger.info("Cleaned up {} expired temp files", deleted)
    return deleted
