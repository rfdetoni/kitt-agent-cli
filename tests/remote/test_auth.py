import unittest

from kitt.remote.auth import PairingAuth


class PairingAuthTests(unittest.TestCase):
    def setUp(self):
        self.now = [1000.0]
        self.auth = PairingAuth(
            pairing_ttl_seconds=60,
            session_ttl_seconds=300,
            clock=lambda: self.now[0],
        )

    def test_pair_auth_csrf_and_logout(self):
        result = self.auth.pair(self.auth.pairing_code, "127.0.0.1")
        self.assertIsNotNone(result)
        token, csrf, expires = result
        self.assertEqual(expires, 1300.0)
        self.assertIsNotNone(self.auth.authenticate(token))
        self.assertTrue(self.auth.validate_csrf(token, csrf))
        self.assertFalse(self.auth.validate_csrf(token, csrf + "x"))
        self.assertTrue(self.auth.logout(token))
        self.assertIsNone(self.auth.authenticate(token))

    def test_csrf_is_stable_across_tabs(self):
        token, csrf, _ = self.auth.pair(self.auth.pairing_code, "127.0.0.1")
        tab_two = self.auth.refresh_csrf(token)
        self.assertEqual(csrf, tab_two)
        self.assertTrue(self.auth.validate_csrf(token, csrf))
        self.assertTrue(self.auth.validate_csrf(token, tab_two))

    def test_pairing_and_session_expire(self):
        code = self.auth.pairing_code
        self.now[0] = 1061.0
        self.assertIsNone(self.auth.pair(code, "127.0.0.1"))
        self.auth.rotate_pairing_code()
        token, _, _ = self.auth.pair(self.auth.pairing_code, "127.0.0.1")
        self.now[0] += 301
        self.assertIsNone(self.auth.authenticate(token))

    def test_wrong_code_is_rejected(self):
        self.assertIsNone(self.auth.pair("00000000", "127.0.0.1") if self.auth.pairing_code != "00000000" else self.auth.pair("99999999", "127.0.0.1"))


if __name__ == "__main__":
    unittest.main()
