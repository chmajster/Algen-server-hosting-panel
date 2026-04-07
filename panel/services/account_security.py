from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime

from flask import current_app

from panel.extensions import db
from panel.models import TwoFactorBackupCode, User, UserSession


def _secret_pepper() -> str:
    return str(current_app.config.get("SECRET_KEY", ""))


def _hash_value(prefix: str, value: str) -> str:
    payload = f"{prefix}:{value}:{_secret_pepper()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalize_backup_code(value: str | None) -> str:
    raw = "".join(ch for ch in (value or "") if ch.isalnum()).upper()
    return raw


def generate_backup_codes(*, user: User, count: int = 10) -> list[str]:
    TwoFactorBackupCode.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    codes: list[str] = []
    for _ in range(max(1, count)):
        code = secrets.token_hex(4).upper()
        codes.append(code)
        db.session.add(
            TwoFactorBackupCode(
                user_id=user.id,
                code_hash=_hash_value(f"backup-code:{user.id}", _normalize_backup_code(code)),
            )
        )
    return codes


def consume_backup_code(*, user: User, code: str | None) -> bool:
    normalized = _normalize_backup_code(code)
    if not normalized:
        return False

    code_hash = _hash_value(f"backup-code:{user.id}", normalized)
    row = (
        TwoFactorBackupCode.query.filter_by(user_id=user.id, code_hash=code_hash)
        .filter(TwoFactorBackupCode.used_at.is_(None))
        .first()
    )
    if row is None:
        return False
    row.used_at = datetime.utcnow()
    db.session.add(row)
    return True


def remaining_backup_codes_count(user: User) -> int:
    return (
        TwoFactorBackupCode.query.filter_by(user_id=user.id)
        .filter(TwoFactorBackupCode.used_at.is_(None))
        .count()
    )


def issue_user_session(*, user: User, ip_address: str | None, user_agent: str | None) -> tuple[str, UserSession]:
    plain_token = secrets.token_urlsafe(40)
    token_hash = _hash_value("session", plain_token)
    row = UserSession(
        user=user,
        session_token_hash=token_hash,
        ip_address=(ip_address or "")[:45] or None,
        user_agent=(user_agent or "")[:500] or None,
        last_activity_at=datetime.utcnow(),
    )
    db.session.add(row)
    return plain_token, row


def hash_session_token(plain_token: str | None) -> str | None:
    token = (plain_token or "").strip()
    if not token:
        return None
    return _hash_value("session", token)


def get_active_session_by_plain_token(plain_token: str | None) -> UserSession | None:
    token_hash = hash_session_token(plain_token)
    if token_hash is None:
        return None
    return UserSession.query.filter_by(session_token_hash=token_hash).filter(UserSession.revoked_at.is_(None)).first()


def touch_session(plain_token: str | None) -> None:
    session_row = get_active_session_by_plain_token(plain_token)
    if session_row is None:
        return
    session_row.last_activity_at = datetime.utcnow()


def revoke_session(*, session_row: UserSession) -> None:
    if session_row.revoked_at is None:
        session_row.revoked_at = datetime.utcnow()
        db.session.add(session_row)


def revoke_all_sessions_for_user(*, user: User, except_plain_token: str | None = None) -> int:
    excluded_hash = hash_session_token(except_plain_token)
    sessions = UserSession.query.filter_by(user_id=user.id).filter(UserSession.revoked_at.is_(None)).all()
    revoked = 0
    for session_row in sessions:
        if excluded_hash and hmac.compare_digest(session_row.session_token_hash, excluded_hash):
            continue
        session_row.revoked_at = datetime.utcnow()
        revoked += 1
    return revoked
