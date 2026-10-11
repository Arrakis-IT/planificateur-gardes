# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Password hashing (argon2id), opaque tokens and password policy."""

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

# argon2-cffi defaults follow RFC 9106 low-memory profile (argon2id).
_hasher = PasswordHasher()

# Pre-computed hash used to spend the same time when the email does not exist,
# so response timing does not reveal which accounts exist.
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))


def hash_password(password: str) -> str:
    """Hash a password with argon2id."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Return True if `password` matches `password_hash`. Never raises on bad input."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def dummy_verify(password: str) -> None:
    """Burn the same CPU time as a real verification (anti user-enumeration)."""
    verify_password(_DUMMY_HASH, password)


def needs_rehash(password_hash: str) -> bool:
    """True if the hash was produced with outdated parameters."""
    return _hasher.check_needs_rehash(password_hash)


def generate_token(nbytes: int = 32) -> str:
    """URL-safe random token for sessions and reset links."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """SHA-256 hex digest of a high-entropy token (fine for random tokens, not for passwords)."""
    return hashlib.sha256(token.encode()).hexdigest()


def generate_temporary_password() -> str:
    """Temporary password given out by an admin; the user must change it at next login."""
    return secrets.token_urlsafe(12)  # 16 chars


def derive_key(secret_key: str, purpose: str) -> bytes:
    """Derive an independent sub-key per purpose from APP_SECRET_KEY."""
    return hmac.new(secret_key.encode(), purpose.encode(), hashlib.sha256).digest()


def validate_new_password(password: str, confirm: str, email: str) -> str | None:
    """Return a French error message if the new password is not acceptable, else None."""
    if password != confirm:
        return "Les deux mots de passe ne correspondent pas."
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"Le mot de passe doit contenir au moins {PASSWORD_MIN_LENGTH} caractères."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"Le mot de passe ne peut pas dépasser {PASSWORD_MAX_LENGTH} caractères."
    if password.strip().lower() == email.strip().lower():
        return "Le mot de passe ne peut pas être votre adresse e-mail."
    return None
