from __future__ import annotations

import base64
import hashlib
import importlib
import secrets
from datetime import datetime, timedelta

from flask import current_app

from panel.extensions import db
from panel.models import Client, User, VaultSecret, VaultSecretVersion


SECRET_TYPES = [
    "api_key",
    "database_password",
    "smtp_password",
    "webhook_secret",
    "oauth_client_secret",
    "tls_private_key",
    "other",
]


def _normalized_text(value: str | None, *, limit: int = 255) -> str:
    return (value or "").strip()[:limit]


def _load_fernet():
    try:
        module = importlib.import_module("cryptography.fernet")
        Fernet = getattr(module, "Fernet")
    except Exception as exc:  # pragma: no cover - depends on environment packages
        raise RuntimeError("Brak pakietu cryptography (Fernet) dla vault secrets.") from exc

    raw_key = (
        _normalized_text(str(current_app.config.get("SECRETS_VAULT_KEY") or ""), limit=500)
        or _normalized_text(str(current_app.config.get("SECRET_KEY") or ""), limit=500)
        or "change-me"
    )
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    fernet_key = base64.urlsafe_b64encode(digest)
    return Fernet(fernet_key)


def encrypt_secret_value(value: str) -> str:
    fernet = _load_fernet()
    token = fernet.encrypt((value or "").encode("utf-8"))
    return token.decode("utf-8")


def decrypt_secret_value(value_encrypted: str) -> str:
    fernet = _load_fernet()
    raw = fernet.decrypt((value_encrypted or "").encode("utf-8"))
    return raw.decode("utf-8")


def fingerprint_secret_value(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _current_secret_version(secret: VaultSecret) -> VaultSecretVersion | None:
    return (
        VaultSecretVersion.query.filter_by(secret_id=secret.id, is_current=True)
        .order_by(VaultSecretVersion.version.desc(), VaultSecretVersion.id.desc())
        .first()
    )


def create_secret(
    *,
    client: Client | None,
    name: str,
    secret_type: str,
    plain_value: str,
    created_by: User | None,
    rotation_interval_days: int | None,
    description: str | None,
) -> VaultSecret:
    normalized_name = _normalized_text(name, limit=120)
    if not normalized_name:
        raise ValueError("Nazwa sekretu jest wymagana.")

    normalized_type = _normalized_text(secret_type, limit=64).lower() or "other"
    if normalized_type not in SECRET_TYPES:
        normalized_type = "other"

    query = VaultSecret.query.filter_by(name=normalized_name)
    if client is None:
        query = query.filter(VaultSecret.client_id.is_(None))
    else:
        query = query.filter(VaultSecret.client_id == client.id)
    existing = query.first()
    if existing is not None:
        raise ValueError("Sekret o tej nazwie juz istnieje dla wskazanego scope.")

    now = datetime.utcnow()
    interval = int(rotation_interval_days) if rotation_interval_days is not None else None
    if interval is not None and interval <= 0:
        interval = None

    secret = VaultSecret(
        client=client,
        name=normalized_name,
        secret_type=normalized_type,
        description=_normalized_text(description, limit=255) or None,
        status="active",
        current_version=1,
        rotation_interval_days=interval,
        last_rotated_at=now,
        next_rotation_due_at=(now + timedelta(days=interval)) if interval else None,
        created_by=created_by,
        updated_by=created_by,
    )
    db.session.add(secret)
    db.session.flush()

    version = VaultSecretVersion(
        secret=secret,
        version=1,
        value_encrypted=encrypt_secret_value(plain_value),
        value_fingerprint=fingerprint_secret_value(plain_value),
        is_current=True,
        metadata_json={"one_time_reveal_consumed": False},
        created_by=created_by,
    )
    db.session.add(version)
    return secret


def rotate_secret(
    *,
    secret: VaultSecret,
    plain_value: str,
    rotated_by: User | None,
    reason: str | None = None,
) -> VaultSecretVersion:
    current = _current_secret_version(secret)
    next_version = 1 if current is None else int(current.version) + 1

    VaultSecretVersion.query.filter_by(secret_id=secret.id, is_current=True).update(
        {"is_current": False}, synchronize_session=False
    )

    row = VaultSecretVersion(
        secret=secret,
        version=next_version,
        value_encrypted=encrypt_secret_value(plain_value),
        value_fingerprint=fingerprint_secret_value(plain_value),
        is_current=True,
        rotated_reason=_normalized_text(reason, limit=255) or None,
        metadata_json={"one_time_reveal_consumed": False},
        created_by=rotated_by,
    )
    db.session.add(row)

    now = datetime.utcnow()
    secret.current_version = next_version
    secret.last_rotated_at = now
    secret.updated_by = rotated_by
    if secret.rotation_interval_days and int(secret.rotation_interval_days) > 0:
        secret.next_rotation_due_at = now + timedelta(days=int(secret.rotation_interval_days))
    else:
        secret.next_rotation_due_at = None
    return row


def reveal_secret_value(secret: VaultSecret, *, revealed_by: User | None) -> str:
    current = _current_secret_version(secret)
    if current is None:
        raise ValueError("Sekret nie ma aktywnej wersji.")

    metadata_json = dict(current.metadata_json or {})
    if bool(metadata_json.get("one_time_reveal_consumed")):
        raise ValueError("Sekret tej wersji zostal juz ujawniony jednorazowo.")

    secret.last_revealed_at = datetime.utcnow()
    secret.updated_by = revealed_by
    metadata_json["one_time_reveal_consumed"] = True
    metadata_json["one_time_revealed_at"] = secret.last_revealed_at.isoformat() if secret.last_revealed_at else None
    current.metadata_json = metadata_json
    return decrypt_secret_value(current.value_encrypted)


def due_rotation_secrets(*, now: datetime | None = None, limit: int = 200) -> list[VaultSecret]:
    reference = now or datetime.utcnow()
    return (
        VaultSecret.query.filter(VaultSecret.status == "active")
        .filter(VaultSecret.next_rotation_due_at.is_not(None))
        .filter(VaultSecret.next_rotation_due_at <= reference)
        .order_by(VaultSecret.next_rotation_due_at.asc(), VaultSecret.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )


def run_rotation_schedule(*, actor: User | None = None, auto_rotate: bool = False, limit: int = 200) -> dict:
    rows = due_rotation_secrets(limit=limit)
    rotated = 0
    due = 0
    errors = 0

    for row in rows:
        due += 1
        if not auto_rotate:
            continue
        try:
            rotate_secret(
                secret=row,
                plain_value=secrets.token_urlsafe(32),
                rotated_by=actor,
                reason="automatic_rotation_schedule",
            )
            rotated += 1
        except Exception:
            errors += 1

    return {
        "due": due,
        "rotated": rotated,
        "errors": errors,
        "auto_rotate": bool(auto_rotate),
    }
