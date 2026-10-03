import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "verify_usb_backup", Path(__file__).resolve().parents[1] / "scripts/verify_usb_backup.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def row(identifier, path, data):
    return {"id": identifier, "path": path, "sha256": hashlib.sha256(data).hexdigest(),
            "downloaded_size": len(data), "upload_status": "SUCCEEDED"}


class USBTests(unittest.TestCase):
    def test_content_matching_not_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "renamed.mp3").write_bytes(b"original")
            (root / "bad.mp3").write_bytes(b"wrong")
            rows = [row("1", "old.mp3", b"original"), row("2", "bad.mp3", b"right"),
                    row("3", "absent.mp3", b"absent"), {"id": "4"}]
            result = verifier.verify(rows, root)
            self.assertEqual([r["status"] for r in result["items"]],
                             ["verified", "different", "not_found", "unverifiable"])
            self.assertEqual(result["items"][0]["local_paths"], ["renamed.mp3"])
            self.assertEqual((root / "bad.mp3").read_bytes(), b"wrong")

    def test_read_failure_is_not_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "song.mp3").write_bytes(b"original")
            with patch.object(verifier, "hash_file", side_effect=OSError("Read failed")):
                result = verifier.verify([row("1", "song.mp3", b"original")], root)
            self.assertEqual(result["items"][0]["status"], "unreadable")
            self.assertFalse(result["scan_complete"])


if __name__ == "__main__":
    unittest.main()
