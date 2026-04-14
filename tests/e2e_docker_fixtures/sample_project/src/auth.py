"""Authentication helpers used by handlers."""

from __future__ import annotations

import hashlib
import hmac


def verify_password(plaintext: str, hashed: str, salt: str) -> bool:
    """Constant-time check that ``plaintext`` hashes to ``hashed`` with ``salt``.

    Uses SHA-256 over salt || plaintext. Designed for the sample project's
    login flow — not production-grade.
    """
    digest = hashlib.sha256((salt + plaintext).encode("utf-8")).hexdigest()
    return hmac.compare_digest(digest, hashed)


def hash_password(plaintext: str, salt: str) -> str:
    """Return the SHA-256 digest of ``salt || plaintext`` as hex."""
    return hashlib.sha256((salt + plaintext).encode("utf-8")).hexdigest()
