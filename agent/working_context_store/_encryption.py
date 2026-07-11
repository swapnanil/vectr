"""
Field-level encryption for note content.

When VECTR_ENCRYPT_KEY is set, note content is encrypted at write time and
decrypted at read time using Fernet (AES-128-CBC + HMAC-SHA256).
The raw passphrase is never stored — a PBKDF2-derived key is used instead.

Requires: pip install vectr[encryption]  (cryptography>=43)

If a note was stored plaintext before encryption was enabled, decrypt()
detects the invalid token and returns the raw text — no data loss.
"""
from __future__ import annotations

import os
import re


class _NoteEncryptor:
    """Fernet-based field-level encryptor for note content."""

    _SALT = b"vectr-notes-v1\x00"  # fixed, non-secret derivation salt

    def __init__(self, passphrase: str) -> None:
        import base64
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=self._SALT,
            iterations=480_000,  # OWASP 2023 recommendation for SHA-256
        )
        key = base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, stored: str) -> str:
        """Decrypt, or return plaintext unchanged if the value was never encrypted."""
        try:
            return self._fernet.decrypt(stored.encode("ascii")).decode("utf-8")
        except Exception:
            # Note was stored before encryption was enabled — return as-is
            return stored


def _key_from_keyring() -> str:
    """Read the encryption passphrase from the OS keychain, if the optional
    `keyring` dependency is installed and a value is stored under
    service "vectr", username "encrypt-key". Returns "" on any failure —
    keychain sourcing is a best-effort convenience, never a hard requirement.
    """
    try:
        import keyring  # optional dependency (pip install vectr[encryption])
    except Exception:
        return ""
    try:
        return keyring.get_password("vectr", "encrypt-key") or ""
    except Exception:
        return ""


def _build_encryptor() -> _NoteEncryptor | None:
    """Return a _NoteEncryptor when an encryption passphrase is available, else
    None (encryption off — the default). Sourcing precedence: the
    VECTR_ENCRYPT_KEY environment variable wins; if it is unset, the OS keychain
    is consulted (env or OS keychain, per the security design)."""
    key = os.getenv("VECTR_ENCRYPT_KEY", "") or _key_from_keyring()
    return _NoteEncryptor(key) if key else None


# Matches file paths in note text — relative (foo/bar.py) and absolute (/usr/local/file.py).
# False positives that don't exist are skipped during staleness stat().
_FILE_PATH_RE = re.compile(
    r'(?<![:/\w])'                                         # not preceded by :, /, or word char
    r'((?:/[a-zA-Z0-9_.][a-zA-Z0-9_.\-]*)+'              # absolute: /foo/bar/baz
    r'|[a-zA-Z0-9_.][a-zA-Z0-9_./\-]*(?:/[a-zA-Z0-9_.][a-zA-Z0-9_.\-]*)+)'  # relative: foo/bar
)


def _extract_file_paths(text: str) -> list[str]:
    """Extract plausible file paths from note text (deduplicated, order-preserving)."""
    seen: set[str] = set()
    result = []
    for raw in _FILE_PATH_RE.findall(text):
        path = raw.rstrip("/.")
        if len(path) > 3 and path not in seen:
            seen.add(path)
            result.append(path)
    return result
