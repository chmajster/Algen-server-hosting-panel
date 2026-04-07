from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

from flask import current_app

from panel.extensions import db
from panel.models import ApiToken, User


TOKEN_PREFIX = "hp"


def _token_hash(secret_part: str) -> str:
    pepper = str(current_app.config.get("SECRET_KEY", ""))
    payload = f"{secret_part}:{pepper}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def issue_api_token(*, user: User, name: str) -> tuple[ApiToken, str]:
    secret_part = secrets.token_urlsafe(24)
    prefix = secret_part[:12]
    token = ApiToken(user=user, name=name.strip(), token_prefix=prefix, token_hash=_token_hash(secret_part))
    db.session.add(token)
    db.session.flush()
    plain = f"{TOKEN_PREFIX}_{token.id}_{secret_part}"
    return token, plain


def parse_bearer_token(header_value: str | None) -> str | None:
    raw = (header_value or "").strip()
    if not raw:
        return None
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw or None


def authenticate_api_token(raw_token: str | None) -> User | None:
    token_value = (raw_token or "").strip()
    if not token_value:
        return None
    parts = token_value.split("_", 2)
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX:
        return None
    _, token_id_raw, secret_part = parts
    if not token_id_raw.isdigit() or not secret_part:
        return None

    token = ApiToken.query.get(int(token_id_raw))
    if token is None or token.revoked_at is not None:
        return None
    expected = _token_hash(secret_part)
    if not hmac.compare_digest(expected, token.token_hash):
        return None

    token.last_used_at = datetime.utcnow()
    db.session.commit()
    return token.user


def revoke_api_token(token: ApiToken) -> None:
    token.revoked_at = datetime.utcnow()
    db.session.add(token)
