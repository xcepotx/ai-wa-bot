"""Secret encryption helpers for provider credentials.

Uses Fernet encryption. In production, set PROVIDER_CREDENTIAL_SECRET to a
stable secret. If missing, a key is derived from JWT_SECRET as a dev fallback.
"""
import os
import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet


def _derive_key(raw: str) -> bytes:
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet() -> Fernet:
    secret = os.getenv("PROVIDER_CREDENTIAL_SECRET") or os.getenv("JWT_SECRET") or "dev-provider-credential-secret"
    secret = secret.strip()

    # Accept already-generated Fernet key.
    try:
        if len(secret) == 44:
            return Fernet(secret.encode("utf-8"))
    except Exception:
        pass

    return Fernet(_derive_key(secret))


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return _fernet().decrypt(value.encode("utf-8")).decode("utf-8")


def secret_last4(value: Optional[str]) -> str:
    if not value:
        return ""
    return str(value)[-4:]
