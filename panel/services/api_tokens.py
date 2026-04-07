from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

from flask import current_app

from panel.extensions import db
from panel.models import ApiToken, User


TOKEN_PREFIX = "hp"
API_TOKEN_SCOPES = [
    ("profile:read", "profile:read"),
    ("billing:read", "billing:read"),
    ("tickets:read", "tickets:read"),
    ("tickets:write", "tickets:write"),
    ("backups:read", "backups:read"),
    ("monitoring:read", "monitoring:read"),
    ("status:read", "status:read"),
    ("events:read", "events:read"),
]


def api_scope_values() -> list[str]:
    return [value for value, _label in API_TOKEN_SCOPES]


def normalize_api_scopes(raw_values: list[str] | tuple[str, ...] | None, *, fallback_full: bool = False) -> list[str]:
    allowed = set(api_scope_values())
    normalized = [value.strip() for value in (raw_values or []) if (value or "").strip() in allowed]
    normalized = list(dict.fromkeys(normalized))
    if normalized:
        return normalized
    if fallback_full:
        return sorted(allowed)
    return []


def _token_hash(secret_part: str) -> str:
    pepper = str(current_app.config.get("SECRET_KEY", ""))
    payload = f"{secret_part}:{pepper}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def issue_api_token(*, user: User, name: str, scopes: list[str] | None = None) -> tuple[ApiToken, str]:
    secret_part = secrets.token_urlsafe(24)
    prefix = secret_part[:12]
    token = ApiToken(
        user=user,
        name=name.strip(),
        token_prefix=prefix,
        token_hash=_token_hash(secret_part),
        scopes_json=normalize_api_scopes(scopes, fallback_full=bool(user.is_staff)),
    )
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
    token = authenticate_api_token_record(raw_token)
    if token is None:
        return None
    return token.user


def authenticate_api_token_record(raw_token: str | None) -> ApiToken | None:
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
    return token


def revoke_api_token(token: ApiToken) -> None:
    token.revoked_at = datetime.utcnow()
    db.session.add(token)
