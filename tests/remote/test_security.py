import unittest

from kitt.remote.security import (
    SlidingWindowLimiter,
    is_allowed_host,
    is_private_client,
    origin_matches_host,
)


class RemoteSecurityTests(unittest.TestCase):
    def test_private_client_boundary(self):
        for value in ("127.0.0.1", "::1", "192.168.1.20", "10.0.0.4", "172.16.0.2"):
            self.assertTrue(is_private_client(value), value)
        self.assertFalse(is_private_client("8.8.8.8"))
        self.assertFalse(is_private_client("not-an-ip"))

    def test_host_header_is_fail_closed(self):
        self.assertTrue(is_allowed_host("localhost:7337"))
        self.assertTrue(is_allowed_host("192.168.1.50:7337"))
        self.assertTrue(is_allowed_host("[::1]:7337"))
        self.assertFalse(is_allowed_host("evil.example"))
        self.assertFalse(is_allowed_host("8.8.8.8:7337"))

    def test_origin_must_match_host(self):
        self.assertTrue(origin_matches_host(None, "192.168.1.50:7337"))
        self.assertTrue(origin_matches_host("http://192.168.1.50:7337", "192.168.1.50:7337"))
        self.assertFalse(origin_matches_host("http://evil.example", "192.168.1.50:7337"))

    def test_rate_limiter_is_bounded(self):
        limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
        self.assertTrue(limiter.allow("client"))
        self.assertTrue(limiter.allow("client"))
        self.assertFalse(limiter.allow("client"))


if __name__ == "__main__":
    unittest.main()
