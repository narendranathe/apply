"""Fixture-only source policy decisions, never a transport or provider grant."""
import unittest
from ipaddress import IPv6Address
from unittest.mock import PropertyMock, patch

from cloud.technical_source_policy import SourcePolicyBlocked, evaluate_source_path


class TechnicalSourcePolicyTests(unittest.TestCase):
    def test_allowlisted_public_dns_is_only_a_candidate_not_network_authorization(self):
        hops = [{"url": "https://engineering.example.test/post?id=7", "addresses": ["93.184.216.34"]}]
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            result = evaluate_source_path(hops, {"engineering.example.test"})
        self.assertEqual(result["decision"], "candidate")
        self.assertFalse(result["transport_pinning_verified"])
        self.assertFalse(result["provider_grant_verified"])
        self.assertFalse(result["robots_permission_verified"])
        self.assertFalse(result["redirect_chain_independently_verified"])

    def test_allowlist_is_exact_for_every_redirect(self):
        start = {"url": "https://engineering.example.test/post", "addresses": ["93.184.216.34"]}
        for target in ("https://engineering.example.test.attacker.example/post", "https://other.example.test/post"):
            with self.subTest(target=target), self.assertRaises(SourcePolicyBlocked):
                evaluate_source_path([start, {"url": target, "addresses": ["93.184.216.34"]}],
                                     {"engineering.example.test"})
        result = evaluate_source_path([start, {"url": "https://docs.example.test/post", "addresses": ["1.1.1.1"]}],
                                      {"engineering.example.test", "docs.example.test"})
        self.assertEqual(result["decision"], "candidate")

    def test_private_reserved_and_mixed_dns_answers_fail_closed_at_any_hop(self):
        forbidden = ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.0.2.1", "::1", "fc00::1",
                     "fe80::1", "::ffff:10.0.0.1", "2001:db8::1", "not-an-ip"]
        for address in forbidden:
            with self.subTest(address=address), self.assertRaises(SourcePolicyBlocked):
                evaluate_source_path([{"url": "https://example.test/a", "addresses": ["1.1.1.1", address]}], {"example.test"})

    def test_missing_and_unbounded_inputs_are_denied_without_network(self):
        for hops, hosts in (([], {"example.test"}), ([{"url": "https://example.test/", "addresses": []}], {"example.test"}),
                            ([{"url": "https://example.test/", "addresses": ["1.1.1.1"]}], set()),
                            ([{"url": "https://example.test/", "addresses": ["1.1.1.1"]}] * 5, {"example.test"})):
            with self.subTest(hops=hops), self.assertRaises(SourcePolicyBlocked):
                evaluate_source_path(hops, hosts)

    def test_insecure_credentials_ip_literals_and_nondefault_ports_are_denied(self):
        urls = ["http://example.test/", "https://user@example.test/", "https://example.test:8443/",
                "https://1.1.1.1/", "https://example.test/%0a", "https://example.test/a#fragment",
                "https://example.test/a\u2003b", "https://127.1/"]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(SourcePolicyBlocked):
                evaluate_source_path([{"url": url, "addresses": ["1.1.1.1"]}], {"example.test"})
        for literal in ("1.1.1.1", "127.1", "0177.0.0.1"):
            with self.subTest(allowlist=literal), self.assertRaises(SourcePolicyBlocked):
                evaluate_source_path([{"url": f"https://{literal}/", "addresses": ["1.1.1.1"]}], {literal})

    def test_public_ipv6_is_allowed_but_multicast_and_tunneled_addresses_are_not(self):
        hop = {"url": "https://example.test/", "addresses": ["2606:4700:4700::1111"]}
        self.assertEqual(evaluate_source_path([hop], {"example.test"})["decision"], "candidate")
        # NAT64 can translate the embedded 127.0.0.1 to IPv4 loopback even
        # though some Python versions label the IPv6 address globally routed.
        for address in ("224.0.0.1", "2002:0808:0808::1", "64:ff9b::7f00:1", "64:ff9b:1::7f00:1"):
            with self.subTest(address=address), self.assertRaises(SourcePolicyBlocked):
                evaluate_source_path([{**hop, "addresses": [address]}], {"example.test"})

    def test_nat64_is_blocked_even_if_stdlib_classification_changes(self):
        hop = {"url": "https://example.test/", "addresses": ["64:ff9b::7f00:1"]}
        # The prefix must remain blocked if a Python release changes how it
        # classifies this globally reachable translation prefix as reserved.
        with patch.object(IPv6Address, "is_reserved", new_callable=PropertyMock, return_value=False):
            with self.assertRaises(SourcePolicyBlocked):
                evaluate_source_path([hop], {"example.test"})


if __name__ == "__main__":
    unittest.main()
