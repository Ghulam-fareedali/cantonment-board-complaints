"""Encryption for credentials at rest.

`integrations.credentials` holds live OAuth tokens for every client — the keys to
their ad accounts and their pages. Until now they sat in the database in
plaintext, which means a single database read (a stolen dump, a misconfigured
backup, an exposed port) hands an attacker every client's advertising account.
Row-Level Security does not help here: RLS controls *which rows a session sees*,
not what the bytes say once someone has the file.

AES-256-GCM, authenticated, with the tenant and provider bound in as additional
authenticated data:

    {"v": 1, "kid": "a1b2c3d4", "n": "<b64 nonce>", "ct": "<b64 ciphertext>"}

Three properties worth stating plainly:

* **AAD binding.** A ciphertext is tied to `tenant_id:provider`. Copying tenant
  A's encrypted token into tenant B's row does not give B a working token — it
  fails to decrypt. The isolation guarantee survives a direct database write.
* **Rotation.** `kid` is derived from the key itself, so several keys can be
  valid at once: new writes use the primary, old rows still decrypt with a
  retired key until they are re-encrypted. No flag day.
* **Backward compatible.** A plain JSON object with no `v`/`ct` envelope is
  legacy plaintext and is returned as-is. Existing tenants (Beauty Aesthetic, the
  limousine client) keep working the moment this ships, before anything is
  migrated. `scripts/encrypt_integrations.py` converts them when you are ready.

Config:
    CREDENTIAL_ENCRYPTION_KEY       primary key, base64 of 32 random bytes
    CREDENTIAL_ENCRYPTION_KEYS_OLD  comma-separated retired keys (decrypt only)
    REQUIRE_CREDENTIAL_ENCRYPTION   true => refuse to store plaintext at all

    generate a key with:  openssl rand -base64 32
"""
from __future__ import annotations

import base64
import hashlib
import json
import os

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_CRYPTO = True
except ImportError:                                    # pragma: no cover
    HAVE_CRYPTO = False


class CryptoError(Exception):
    pass


def _load(raw: str) -> bytes:
    try:
        key = base64.b64decode(raw.strip(), validate=True)
    except Exception:
        raise CryptoError("credential key is not valid base64")
    if len(key) != 32:
        raise CryptoError(f"credential key must decode to 32 bytes, got {len(key)}")
    return key


def _kid(key: bytes) -> str:
    """Key id derived from the key, so rotation needs no bookkeeping."""
    return hashlib.sha256(b"techdeva-kid" + key).hexdigest()[:8]


def _keys() -> list[bytes]:
    """Primary first, then any retired keys that should still decrypt."""
    out = []
    primary = os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if primary:
        out.append(_load(primary))
    for old in os.environ.get("CREDENTIAL_ENCRYPTION_KEYS_OLD", "").split(","):
        if old.strip():
            out.append(_load(old))
    return out


def enabled() -> bool:
    """True when new credentials can actually be encrypted."""
    return HAVE_CRYPTO and bool(os.environ.get("CREDENTIAL_ENCRYPTION_KEY", "").strip())


def required() -> bool:
    return os.environ.get("REQUIRE_CREDENTIAL_ENCRYPTION", "false").lower() in {"1", "true", "yes"}


def is_encrypted(value) -> bool:
    return isinstance(value, dict) and value.get("v") == 1 and "ct" in value and "n" in value


def _aad(tenant_id, provider) -> bytes:
    return f"{tenant_id}:{provider}".encode()


def encrypt_json(obj: dict, tenant_id, provider: str) -> dict:
    """Return the storable value: an envelope when a key is configured.

    With no key it returns the object unchanged so nothing breaks mid-migration —
    unless REQUIRE_CREDENTIAL_ENCRYPTION=true, which refuses instead. The preflight
    check reports plaintext storage as a launch blocker either way.
    """
    if not enabled():
        if required():
            raise CryptoError(
                "REQUIRE_CREDENTIAL_ENCRYPTION=true but no usable "
                "CREDENTIAL_ENCRYPTION_KEY" + ("" if HAVE_CRYPTO else
                                               " (the 'cryptography' package is missing)"))
        return obj
    key = _keys()[0]
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, json.dumps(obj).encode(), _aad(tenant_id, provider))
    return {"v": 1, "kid": _kid(key),
            "n": base64.b64encode(nonce).decode(),
            "ct": base64.b64encode(ct).decode()}


def decrypt_json(value, tenant_id, provider: str) -> dict:
    """Envelope -> plaintext dict. Legacy plaintext passes straight through."""
    if not is_encrypted(value):
        return value or {}
    if not HAVE_CRYPTO:
        raise CryptoError("stored credentials are encrypted but 'cryptography' is missing")
    keys = _keys()
    if not keys:
        raise CryptoError("stored credentials are encrypted but no key is configured")
    nonce = base64.b64decode(value["n"])
    ct = base64.b64decode(value["ct"])
    wanted = value.get("kid")
    # try the matching key first, then every other key (covers an un-tagged rotation)
    ordered = sorted(keys, key=lambda k: _kid(k) != wanted)
    for key in ordered:
        try:
            return json.loads(AESGCM(key).decrypt(nonce, ct, _aad(tenant_id, provider)))
        except Exception:
            continue
    raise CryptoError(
        f"could not decrypt credentials for {provider} (key id {wanted}) — wrong key, "
        f"or the row was moved between tenants")
