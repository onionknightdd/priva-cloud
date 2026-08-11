import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from priva_agent_runner.routers.agent import _validate_attachments
from priva_agent_runner.services.claude_sdk.service import _build_prompt_with_attachments
from priva_agent_runner.services.temp_files import save_temp_file


class AgentAttachmentValidationTests(unittest.TestCase):
    def test_allows_files_in_workspace_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            file_path = workspace / "note.txt"
            file_path.write_text("hello", encoding="utf-8")

            result = _validate_attachments(
                [SimpleNamespace(path=str(file_path), name="note.txt")],
                str(workspace),
            )

            self.assertEqual(result, [{"path": str(file_path.resolve()), "name": "note.txt"}])

    def test_allows_uploaded_files_in_temp_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            temp_dir = workspace / "temp"
            temp_dir.mkdir()
            file_path = temp_dir / "upload.pdf"
            file_path.write_bytes(b"%PDF")

            result = _validate_attachments(
                [SimpleNamespace(path=str(file_path), name="upload.pdf")],
                str(workspace),
            )

            self.assertEqual(result, [{"path": str(file_path.resolve()), "name": "upload.pdf"}])

    def test_rejects_files_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as workspace_tmp, tempfile.TemporaryDirectory() as outside_tmp:
            outside_file = Path(outside_tmp) / "secret.txt"
            outside_file.write_text("nope", encoding="utf-8")

            with self.assertRaises(HTTPException) as ctx:
                _validate_attachments(
                    [SimpleNamespace(path=str(outside_file), name="secret.txt")],
                    workspace_tmp,
                )

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertIn("outside workspace", ctx.exception.detail)

    def test_allows_id_bound_image_upload_outside_selected_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            uploads = root / "uploads"
            cwd = root / "project"
            cwd.mkdir()
            png = b"\x89PNG\r\n\x1a\nrest"
            with patch(
                "priva_agent_runner.services.temp_files.get_temp_dir",
                return_value=uploads,
            ):
                attachment_id, _, path, _ = save_temp_file(
                    "alice", "image.png", png
                )
                result = _validate_attachments(
                    [SimpleNamespace(
                        path=path,
                        name="image.png",
                        attachment_id=attachment_id,
                        media_type="image/png",
                        is_image=True,
                    )],
                    str(cwd),
                    "alice",
                )

            self.assertEqual(result[0]["path"], str(Path(path).resolve()))
            self.assertEqual(result[0]["attachment_id"], attachment_id)
            self.assertTrue(result[0]["is_image"])


class AgentAttachmentPromptTests(unittest.TestCase):
    def test_returns_original_prompt_without_attachments(self) -> None:
        self.assertEqual(
            _build_prompt_with_attachments("Summarize this.", None),
            "Summarize this.",
        )

    def test_marks_single_attachment_as_current_turn_input(self) -> None:
        result = _build_prompt_with_attachments(
            "这个文件有什么内容",
            [{"path": "/workspace/admin/current.jsonl", "name": "current.jsonl"}],
        )

        self.assertTrue(
            result.startswith("这个文件有什么内容\n\n<current-turn-attachments>\n")
        )
        self.assertIn(
            "These files are task inputs, not background metadata or system reminders.",
            result,
        )
        self.assertIn(
            'phrases such as "this file", "the file", "这个文件", or "附件" refer to that file',
            result,
        )
        self.assertIn(
            "Do not substitute a file from an earlier conversation turn",
            result,
        )
        self.assertIn("- current.jsonl: /workspace/admin/current.jsonl", result)
        self.assertTrue(result.endswith("</current-turn-attachments>"))
        self.assertLess(
            result.index("这个文件有什么内容"),
            result.index("<current-turn-attachments>"),
        )

    def test_requires_inspection_and_preserves_binary_file_guidance(self) -> None:
        result = _build_prompt_with_attachments(
            "Compare the files.",
            [
                {"path": "/workspace/admin/report.pdf", "name": "report.pdf"},
                {"path": "/workspace/admin/notes.txt", "name": None},
            ],
        )

        self.assertIn("inspect the relevant attached file", result)
        self.assertIn("Never read binary formats", result)
        self.assertIn("`mcp__FileCanvas__register_file`", result)
        self.assertIn("- report.pdf: /workspace/admin/report.pdf", result)
        self.assertIn("- /workspace/admin/notes.txt", result)

    def test_image_attachments_require_the_run_scoped_vision_tool(self) -> None:
        result = _build_prompt_with_attachments(
            "What is in this image?",
            [{
                "path": "/workspace/admin/temp/image.png",
                "name": "image.png",
                "is_image": True,
            }],
        )

        self.assertIn("`mcp__Vision__image_read`", result)
        self.assertIn("using its EXACT path", result)
        self.assertIn("Do not use Read, Bash, Python", result)


if __name__ == "__main__":
    unittest.main()
