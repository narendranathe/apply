"""Offline policy checks on supplied URL/DNS fixtures; never performs a fetch.

Candidate status does not authorize a request. A future transport must verify
provider and robots permissions, resolve and pin addresses for every request,
and independently enforce and verify the full redirect chain.
"""
from ipaddress import ip_address, ip_network
import re
from urllib.parse import urlsplit

GLOBAL_IPV6_UNICAST = ip_network("2000::/3")


class SourcePolicyBlocked(ValueError):
    """Fixed, non-sensitive reason for a source policy refusal."""


def _host(value):
    if not isinstance(value, str) or not value or len(value) > 253 or value != value.lower():
        raise SourcePolicyBlocked("invalid_allowlist")
    labels = value.split(".")
    if (len(labels) < 2 or labels[-1].isdigit()
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)):
        raise SourcePolicyBlocked("invalid_allowlist")
    try:
        ip_address(value)
    except ValueError:
        return value
    raise SourcePolicyBlocked("ip_literal_denied")


def _url_host(url):
    if (not isinstance(url, str) or len(url) > 2048 or any(char.isspace() for char in url)
            or re.search(r"[\x00-\x20\x7f]|%(?:0[0-9a-f]|1[0-9a-f]|7f)", url, re.I)):
        raise SourcePolicyBlocked("invalid_url")
    try:
        parts = urlsplit(url)
        port = parts.port
        if (parts.scheme.lower() != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or port not in (None, 443) or parts.fragment):
            raise SourcePolicyBlocked("invalid_url")
        return _host(parts.hostname.lower())
    except ValueError:
        raise SourcePolicyBlocked("invalid_url") from None


def _public_address(raw):
    if not isinstance(raw, str) or "%" in raw or len(raw) > 45:
        raise SourcePolicyBlocked("address_denied")
    try:
        address = ip_address(raw)
    except ValueError:
        raise SourcePolicyBlocked("address_denied") from None
    if (not address.is_global or address.is_private or address.is_reserved or address.is_multicast
            or address.is_loopback or address.is_link_local or address.is_unspecified
            or (address.version == 6 and (address not in GLOBAL_IPV6_UNICAST
                                          or address.ipv4_mapped or address.sixtofour or address.teredo))):
        raise SourcePolicyBlocked("address_denied")


def evaluate_source_path(hops, allowed_hosts):
    """Check each caller-supplied hop against an exact host allowlist and DNS set.

    All results are provisional because supplied addresses/redirects are not
    authenticated and no network, robots policy or account grant is checked.
    """
    if not isinstance(allowed_hosts, (set, frozenset)) or not 1 <= len(allowed_hosts) <= 100:
        raise SourcePolicyBlocked("invalid_allowlist")
    hosts = {_host(host) for host in allowed_hosts}
    if not isinstance(hops, list) or not 1 <= len(hops) <= 4:
        raise SourcePolicyBlocked("invalid_hops")
    for hop in hops:
        if not isinstance(hop, dict) or set(hop) != {"url", "addresses"}:
            raise SourcePolicyBlocked("invalid_hops")
        if _url_host(hop["url"]) not in hosts:
            raise SourcePolicyBlocked("host_not_allowlisted")
        addresses = hop["addresses"]
        if not isinstance(addresses, list) or not 1 <= len(addresses) <= 16:
            raise SourcePolicyBlocked("address_denied")
        for address in addresses:
            _public_address(address)
    return {"decision": "candidate", "transport_pinning_verified": False,
            "provider_grant_verified": False, "robots_permission_verified": False,
            "redirect_chain_independently_verified": False}
