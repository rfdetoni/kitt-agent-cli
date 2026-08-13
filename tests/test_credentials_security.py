import os
import tempfile
import unittest
from pathlib import Path
from kitt.security.credentials import CredentialRef, CredentialResolver, atomic_write_secure, set_session_credential

class TestCredentialsSecurity(unittest.TestCase):
    def test_credential_ref_redaction(self):
        os.environ["TEST_SECRET_KEY"] = "sk-proj-secret12345"
        ref = CredentialRef("env:TEST_SECRET_KEY")
        self.assertEqual(ref.resolve(), "sk-proj-secret12345")
        self.assertNotIn("sk-proj-secret12345", repr(ref))
        self.assertNotIn("sk-proj-secret12345", str(ref))

    def test_session_credential_resolution(self):
        set_session_credential("sess_key", "session_secret_999")
        ref = CredentialRef("session:sess_key")
        self.assertEqual(ref.resolve(), "session_secret_999")

    def test_atomic_write_secure_permissions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "secure_sub" / "config.json"
            atomic_write_secure(target, '{"secret_ref": "env:KEY"}')
            self.assertTrue(target.exists())
            if os.name == "posix":
                mode = target.stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)
                dir_mode = target.parent.stat().st_mode & 0o777
                self.assertEqual(dir_mode, 0o700)

if __name__ == "__main__":
    unittest.main()
