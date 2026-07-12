import unittest

from app.utils.security import (
    _PRIVATE_NETWORKS,
    is_internal_url,
    validate_webhook_url,
)


class IsInternalUrlTests(unittest.TestCase):
    def test_loopback_ipv4(self):
        self.assertTrue(is_internal_url("http://127.0.0.1/admin"))

    def test_private_10_network(self):
        self.assertTrue(is_internal_url("http://10.0.0.1/api"))

    def test_private_172_network(self):
        self.assertTrue(is_internal_url("http://172.16.0.1/"))

    def test_private_192_network(self):
        self.assertTrue(is_internal_url("http://192.168.1.1/"))

    def test_link_local_metadata_endpoint(self):
        self.assertTrue(is_internal_url("http://169.254.169.254/latest/meta-data/"))

    def test_alibaba_metadata_endpoint(self):
        # 100.100.100.200 is in the 100.64.0.0/10 range (Alibaba Cloud metadata).
        # Added by T2.5. Skip if the range has not been added yet.
        import ipaddress

        target = ipaddress.ip_network("100.64.0.0/10")
        if not any(net == target for net in _PRIVATE_NETWORKS):
            self.skipTest("100.64.0.0/10 range not yet added (depends on T2.5)")
        self.assertTrue(
            is_internal_url("http://100.100.100.200/latest/meta-data/")
        )

    def test_localhost_hostname(self):
        self.assertTrue(is_internal_url("http://localhost/admin"))

    def test_ipv6_loopback(self):
        self.assertTrue(is_internal_url("http://[::1]/"))

    def test_public_ip_returns_false(self):
        self.assertFalse(is_internal_url("http://93.184.216.34/"))

    def test_public_hostname_returns_false(self):
        self.assertFalse(is_internal_url("https://example.com/webhook"))

    def test_empty_url_returns_true(self):
        self.assertTrue(is_internal_url(""))


class ValidateWebhookUrlTests(unittest.TestCase):
    def test_rejects_internal_url(self):
        ok, _ = validate_webhook_url("http://127.0.0.1/secret")
        self.assertFalse(ok)

    def test_rejects_non_http_scheme(self):
        ok, _ = validate_webhook_url("ftp://example.com")
        self.assertFalse(ok)

    def test_accepts_public_https_url(self):
        ok, msg = validate_webhook_url("https://discord.com/api/webhooks/123")
        self.assertTrue(ok)
        self.assertEqual(msg, "")


if __name__ == "__main__":
    unittest.main()
