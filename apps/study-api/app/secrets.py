"""Encryption for provider credentials stored in the database.

AES-256-GCM with a random nonce per value. The row id is bound as associated data, so a
ciphertext copied onto another row fails to decrypt. The key version is stored beside each
value so keys can be rotated: keep old versions readable, write with the newest.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CURRENT_VERSION = 1


class SecretsUnavailable(RuntimeError):
    pass


def _keys(settings):
    raw = settings.secret_key
    if not raw:
        return {}
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError:
        raise SecretsUnavailable('STUDYHUB_SECRET_KEY must be base64.') from None
    if len(key) != 32:
        raise SecretsUnavailable('STUDYHUB_SECRET_KEY must decode to 32 bytes.')
    return {CURRENT_VERSION: key}


def encrypt(settings, plaintext: str, row_id) -> tuple[bytes, bytes, int]:
    keys = _keys(settings)
    if CURRENT_VERSION not in keys:
        raise SecretsUnavailable('Saving API keys needs STUDYHUB_SECRET_KEY. Ask the administrator to set it.')
    nonce = os.urandom(12)
    return AESGCM(keys[CURRENT_VERSION]).encrypt(nonce, plaintext.encode(), str(row_id).encode()), nonce, CURRENT_VERSION


def decrypt(settings, ciphertext, nonce, version, row_id) -> str:
    if ciphertext is None:
        return ''
    key = _keys(settings).get(version)
    if not key:
        raise SecretsUnavailable('The key that protects this API key is not configured.')
    return AESGCM(key).decrypt(bytes(nonce), bytes(ciphertext), str(row_id).encode()).decode()
