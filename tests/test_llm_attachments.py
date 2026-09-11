import base64
import tempfile
import unittest
from pathlib import Path

from kitt.core.attachment_runtime import _retrieval_prompt
from kitt.llm.attachments import (
    AttachmentError,
    attach_to_first_user_message,
    build_content_parts,
    is_binary_attachment_path,
)


class TestLlmAttachments(unittest.TestCase):
    def test_pdf_becomes_openai_input_file_part(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = b"%PDF-1.4\nKITT\n"
            (root / "sample.pdf").write_bytes(payload)

            parts = build_content_parts(root, "resuma", ["sample.pdf"])

            self.assertEqual(parts[0], {"type": "text", "text": "resuma"})
            self.assertEqual(parts[1]["type"], "input_file")
            self.assertEqual(parts[1]["filename"], "sample.pdf")
            self.assertEqual(
                parts[1]["file_data"],
                "data:application/pdf;base64," + base64.b64encode(payload).decode("ascii"),
            )

    def test_image_keeps_image_url_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "image.png").write_bytes(b"png")
            parts = build_content_parts(root, "analise", ["image.png"])
            self.assertEqual(parts[1]["type"], "image_url")
            self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_wire_copy_does_not_mutate_internal_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sample.pdf").write_bytes(b"%PDF")
            messages = [{"role": "user", "content": "explique"}]
            wire = attach_to_first_user_message(root, messages, ["sample.pdf"])
            self.assertEqual(messages[0]["content"], "explique")
            self.assertIsInstance(wire[0]["content"], list)

    def test_outside_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            foreign = Path(outside) / "secret.pdf"
            foreign.write_bytes(b"%PDF")
            with self.assertRaises(AttachmentError):
                build_content_parts(tmp, "read", [str(foreign)])

    def test_binary_context_paths_are_recognized_as_attachments(self):
        self.assertTrue(is_binary_attachment_path("docs/report.pdf"))
        self.assertTrue(is_binary_attachment_path("diagram.PNG"))
        self.assertFalse(is_binary_attachment_path("src/Main.java"))

    def test_retrieval_prompt_removes_only_attachment_references(self):
        prompt = "@docs/report.pdf compare com @src/Main.java e explique"
        sanitized = _retrieval_prompt(prompt, ("docs/report.pdf",))
        self.assertNotIn("report.pdf", sanitized)
        self.assertIn("@src/Main.java", sanitized)
        self.assertIn("compare", sanitized)


if __name__ == "__main__":
    unittest.main()
