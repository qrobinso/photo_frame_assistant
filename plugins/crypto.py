"""
Fernet encryption for plugin secret config values.

The encryption key is auto-generated on first use and stored in
config/plugin_secrets.key (mode 0o600).  It never leaves the server.

Encrypted values are stored as the string "fernet:<base64-token>".
API responses replace these with the sentinel "__set__" so callers can
tell that a secret is present without seeing its value.
"""

import os

from cryptography.fernet import Fernet

SENTINEL = '__set__'
_PREFIX  = 'fernet:'


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def _key_path() -> str:
    base = os.environ.get('CONFIG_PATH') or os.path.join(
        os.path.abspath(os.path.join(os.path.dirname(__file__), '..')), 'config'
    )
    return os.path.join(base, 'plugin_secrets.key')


def _load_or_create_key() -> bytes:
    path = _key_path()
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return f.read().strip()
    key = Fernet.generate_key()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(key)
    os.chmod(path, 0o600)
    return key


# ---------------------------------------------------------------------------
# Primitive encrypt / decrypt
# ---------------------------------------------------------------------------

def encrypt(plaintext: str) -> str:
    """Return "fernet:<token>" for the given plaintext."""
    f = Fernet(_load_or_create_key())
    return _PREFIX + f.encrypt(plaintext.encode()).decode()


def decrypt(stored: str) -> str:
    """Decrypt a "fernet:<token>" value; return non-prefixed values unchanged."""
    if not stored.startswith(_PREFIX):
        return stored
    f = Fernet(_load_or_create_key())
    return f.decrypt(stored[len(_PREFIX):].encode()).decode()


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


# ---------------------------------------------------------------------------
# Config-level helpers (use plugin config_schema to identify secret fields)
# ---------------------------------------------------------------------------

def encrypt_secrets(config: dict, schema: dict) -> dict:
    """Return a copy of config with secret fields encrypted.

    Skips values that are already encrypted or equal to SENTINEL.
    """
    result = dict(config)
    for key, meta in schema.items():
        if not meta.get('secret'):
            continue
        val = result.get(key)
        if val and isinstance(val, str) and not is_encrypted(val) and val != SENTINEL:
            result[key] = encrypt(val)
    return result


def decrypt_secrets(config: dict, schema: dict) -> dict:
    """Return a copy of config with all secret fields decrypted (for plugin execution)."""
    result = dict(config)
    for key, meta in schema.items():
        if not meta.get('secret'):
            continue
        val = result.get(key)
        if val and is_encrypted(val):
            result[key] = decrypt(val)
    return result


def mask_for_api(config: dict) -> dict:
    """Return config safe to send to the browser.

    Any encrypted value is replaced with SENTINEL ("__set__") so the UI
    knows the field has a saved value without exposing the ciphertext.
    """
    return {k: (SENTINEL if is_encrypted(v) else v) for k, v in config.items()}
