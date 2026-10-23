"""Permanent no-create reservations for two uncertain auxiliary observations.

These fixed reservations never expire. Reads and lossless nested evidence in
the correction audit bundle remain available. No lookup or network is needed.
"""
from types import MappingProxyType

RESERVED_CORE_HASHES = MappingProxyType({
    'native-delivery-claim-observation:13eacd409b8c6dca076c4b1f62d9db1bf015b7759cb02360a7f5421fd8896208':
        'fa85910a0c7c8eb1d5a1c94e190f531848b167db331b67354e78a1965bc99876',
    'user-controller-stop-observation:963a71822d95d8712a13e9ccec8522e2fe6046890c122eae07f94497fe0d9c6b':
        '14bb53f9a4ee01f8c45702d03e23cfd4b6a5f80b6c8269417eb97b370ffcb344',
})
RESERVED_RECORD_IDS = frozenset(RESERVED_CORE_HASHES)
NATIVE_SOURCE = '593cab69f7747625fa16ce2899284336a81668a4c8f95e6640990bf018e96413'
STOP_ATTESTATION = '9a702deaad17836d4d5c574d6f9bf7cd9682b0a63f0f305fb97b8ee374a3c1b7'


def reserved_creation(record):
    """Reject the original top-level IDs and alternate-ID copies of their source."""
    if not isinstance(record, dict):
        return False
    if isinstance(record.get('id'), str) and record['id'] in RESERVED_RECORD_IDS:
        return True
    payload = record.get('payload')
    if not isinstance(payload, dict):
        return False
    # Changing the ID or schema cannot launder the same top-level source into
    # a fresh observation. The correction bundle keeps both references nested.
    return (payload.get('source_logical_page_sha256') == NATIVE_SOURCE
            or payload.get('source_attestation_cipher_sha256') == STOP_ATTESTATION)
