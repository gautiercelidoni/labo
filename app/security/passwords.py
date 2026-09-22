"""Hachage des mots de passe (Argon2id) et politique minimale."""
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # Argon2id, paramètres recommandés par argon2-cffi (RFC 9106)

# Empreinte factice utilisée pour égaliser le temps de réponse sur un email inconnu.
_DUMMY_HASH = _hasher.hash("mot-de-passe-factice-pour-temps-constant")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def password_problems(password: str, min_length: int) -> list[str]:
    problems = []
    if len(password) < min_length:
        problems.append(f"Le mot de passe doit contenir au moins {min_length} caractères.")
    if password.lower() == password or password.upper() == password:
        problems.append("Le mot de passe doit mélanger majuscules et minuscules.")
    if not any(c.isdigit() for c in password):
        problems.append("Le mot de passe doit contenir au moins un chiffre.")
    return problems
