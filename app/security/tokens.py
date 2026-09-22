"""Jetons à usage unique envoyés par email.

Le jeton est une valeur aléatoire de 256 bits ; seule son empreinte SHA-256 est stockée.
Un vol de la base ne permet donc pas de réutiliser un lien. L'expiration et l'usage unique
sont vérifiés en base.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets


def new_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_match(token: str, token_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), token_hash)
