"""Public-key sealed intake. Agents receive only the public encryption key."""
import base64
import json
import os

SCHEMA = 'apply-sealed-agent-intake/v1'
CONTEXT = b'narendranathe/apply:agent-intake:v1'
MAX_PLAINTEXT = 32000
MAX_CIPHERTEXT = 48000


def _b64(value):
    return base64.b64encode(value).decode('ascii')


def _raw(value, length):
    result = base64.b64decode(value, validate=True)
    if len(result) != length:
        raise ValueError('invalid_encryption_key_shape')
    return result


def _derive(shared):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=CONTEXT).derive(shared)


def generate_keys():
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption
    private = X25519PrivateKey.generate()
    return (_b64(private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())),
            _b64(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)))


def seal(value, public_key):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > MAX_PLAINTEXT:
        raise ValueError('intake_size_limit_exceeded')
    ephemeral = X25519PrivateKey.generate()
    key = _derive(ephemeral.exchange(X25519PublicKey.from_public_bytes(_raw(public_key, 32))))
    nonce = os.urandom(12)
    return {'schema': SCHEMA, 'ephemeral_public': _b64(ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)),
            'nonce': _b64(nonce), 'ciphertext': _b64(AESGCM(key).encrypt(nonce, data, CONTEXT))}


def unseal(value, private_key):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if (not isinstance(value, dict) or value.get('schema') != SCHEMA
            or len(json.dumps(value).encode('utf-8')) > MAX_CIPHERTEXT):
        raise ValueError('invalid_sealed_intake')
    private = X25519PrivateKey.from_private_bytes(_raw(private_key, 32))
    key = _derive(private.exchange(X25519PublicKey.from_public_bytes(_raw(value['ephemeral_public'], 32))))
    ciphertext = base64.b64decode(value['ciphertext'], validate=True)
    if not 16 <= len(ciphertext) <= MAX_PLAINTEXT + 16:
        raise ValueError('intake_size_limit_exceeded')
    data = AESGCM(key).decrypt(_raw(value['nonce'], 12), ciphertext, CONTEXT)
    return json.loads(data.decode('utf-8'))
