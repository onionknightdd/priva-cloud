import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from priva_agent_runner.services.temp_files import (
    ATTACHMENT_ID_LENGTH,
    METADATA_FILENAME,
    _validate_attachment_id,
    delete_temp_file,
    get_file_by_id,
    get_file_by_uuid,
    list_temp_files,
    sanitize_upload_filename,
    save_temp_file,
    validate_file,
    validate_file_content,
)


class TempFileValidationTests(unittest.TestCase):
    def test_accepts_zip_based_xlsx(self) -> None:
        validate_file_content("report.xlsx", b"PK\x03\x04test-data")

    def test_accepts_ole_based_xls(self) -> None:
        validate_file_content("report.xls", b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1test-data")

    def test_rejects_invalid_xlsx_payload(self) -> None:
        with self.assertRaises(HTTPException) as exc:
            validate_file_content("report.xlsx", b"%TSD-Header-###%not-a-real-xlsx")

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("Invalid .xlsx file", exc.exception.detail)

    def test_accepts_supported_dotfiles(self) -> None:
        validate_file(".env", 10)
        validate_file(".dockerfile", 10)

    def test_accepts_valid_image_and_rejects_mismatched_signature(self) -> None:
        validate_file("photo.png", 12)
        validate_file_content("photo.png", b"\x89PNG\r\n\x1a\nrest")
        with self.assertRaises(HTTPException):
            validate_file_content("photo.png", b"GIF89a-not-a-png")


class TempFileStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self._temp_dir.name) / "uploads"
        target = "priva_agent_runner.services.temp_files.get_temp_dir"
        self._temp_dir_patch = patch(target, return_value=self.upload_dir)
        self._temp_dir_patch.start()

    def tearDown(self) -> None:
        self._temp_dir_patch.stop()
        self._temp_dir.cleanup()

    def test_sanitizes_to_readable_basename(self) -> None:
        self.assertEqual(sanitize_upload_filename("../../reports/季度报告.txt"), "季度报告.txt")
        self.assertEqual(sanitize_upload_filename("folder\\notes\x00.md"), "notes_.md")
        self.assertEqual(sanitize_upload_filename("metadata.json"), "attachment-metadata.json")

        long_name = f"{'文' * 100}.txt"
        safe_name = sanitize_upload_filename(long_name)
        self.assertLessEqual(len(safe_name.encode("utf-8")), 200)
        self.assertTrue(safe_name.endswith(".txt"))

    def test_short_id_directory_storage_lifecycle(self) -> None:
        payload = "hello, attachment".encode()
        attachment_id, safe_name, full_path, size = save_temp_file(
            "alice", "工作记录.md", payload
        )

        attachment_dir = self.upload_dir / attachment_id
        self.assertEqual(len(attachment_id), ATTACHMENT_ID_LENGTH)
        self.assertTrue(
            all(char.isalnum() or char in "-_" for char in attachment_id)
        )
        self.assertEqual(safe_name, "工作记录.md")
        self.assertEqual(Path(full_path), (attachment_dir / safe_name).resolve())
        self.assertEqual(Path(full_path).read_bytes(), payload)
        self.assertEqual(size, len(payload))
        self.assertFalse((self.upload_dir / ".index.jsonl").exists())

        metadata = json.loads(
            (attachment_dir / METADATA_FILENAME).read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["attachment_id"], attachment_id)
        self.assertNotIn("uuid", metadata)
        self.assertEqual(metadata["original_name"], "工作记录.md")
        self.assertEqual(metadata["safe_name"], safe_name)

        entries = list_temp_files("alice")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["attachment_id"], attachment_id)
        self.assertEqual(entries[0]["uuid"], attachment_id)
        self.assertEqual(entries[0]["safe_name"], safe_name)
        self.assertEqual(entries[0]["stored_name"], safe_name)
        self.assertEqual(entries[0]["path"], full_path)

        file_path, original_name, mime_type = get_file_by_id("alice", attachment_id)
        self.assertEqual(file_path, full_path)
        self.assertEqual(original_name, "工作记录.md")
        self.assertEqual(mime_type, "text/markdown")

        delete_temp_file("alice", attachment_id)
        self.assertFalse(attachment_dir.exists())
        with self.assertRaises(HTTPException) as exc:
            get_file_by_id("alice", attachment_id)
        self.assertEqual(exc.exception.status_code, 404)

    def test_validates_short_and_legacy_ids(self) -> None:
        _validate_attachment_id("Abcdef12_-xy")
        _validate_attachment_id("a" * 32)
        for invalid in ("too-short", "a" * 13, "invalid/id!"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(HTTPException) as exc:
                    _validate_attachment_id(invalid)
                self.assertEqual(exc.exception.status_code, 400)

    def test_reads_previous_uuid_directory_layout(self) -> None:
        legacy_uuid = "b" * 32
        attachment_dir = self.upload_dir / legacy_uuid
        attachment_dir.mkdir(parents=True)
        payload_path = attachment_dir / "previous.md"
        payload_path.write_text("previous", encoding="utf-8")
        metadata = {
            "uuid": legacy_uuid,
            "original_name": "previous.md",
            "safe_name": "previous.md",
            "ext": ".md",
            "size": 8,
            "mime_type": "text/markdown",
            "upload_date": "2026-08-08",
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        (attachment_dir / METADATA_FILENAME).write_text(
            json.dumps(metadata), encoding="utf-8"
        )

        entries = list_temp_files("alice")
        self.assertEqual(entries[0]["attachment_id"], legacy_uuid)
        self.assertEqual(
            get_file_by_uuid("alice", legacy_uuid),
            (str(payload_path.resolve()), "previous.md", "text/markdown"),
        )

    def test_reads_and_deletes_legacy_index_layout(self) -> None:
        file_uuid = "a" * 32
        upload_date = "2026-08-08"
        stored_name = f"{file_uuid}.txt"
        legacy_dir = self.upload_dir / upload_date
        legacy_dir.mkdir(parents=True)
        legacy_path = legacy_dir / stored_name
        legacy_path.write_text("legacy", encoding="utf-8")
        entry = {
            "uuid": file_uuid,
            "original_name": "legacy.txt",
            "stored_name": stored_name,
            "ext": ".txt",
            "size": 6,
            "mime_type": "text/plain",
            "upload_date": upload_date,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
        }
        (self.upload_dir / ".index.jsonl").write_text(
            json.dumps(entry) + "\n", encoding="utf-8"
        )

        entries = list_temp_files("alice")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["attachment_id"], file_uuid)
        self.assertEqual(entries[0]["path"], str(legacy_path))
        self.assertEqual(
            get_file_by_uuid("alice", file_uuid),
            (str(legacy_path), "legacy.txt", "text/plain"),
        )

        delete_temp_file("alice", file_uuid)
        self.assertFalse(legacy_path.exists())
        self.assertEqual(list_temp_files("alice"), [])


if __name__ == "__main__":
    unittest.main()
