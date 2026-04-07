from __future__ import annotations

import base64
import hashlib
from datetime import datetime
from pathlib import Path

from flask import current_app

from panel.extensions import db
from panel.models import Client, ClientSSHKey, User


class SSHKeyError(RuntimeError):
    pass


_ALLOWED_KEY_TYPES = {
    "ssh-rsa",
    "ssh-ed25519",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
}


def _split_public_key(value: str) -> tuple[str, str, str | None]:
    chunks = (value or "").strip().split()
    if len(chunks) < 2:
        raise SSHKeyError("Klucz publiczny musi miec format: <type> <base64> [comment].")

    key_type = chunks[0].strip()
    key_data = chunks[1].strip()
    comment = " ".join(chunks[2:]).strip() or None
    if key_type not in _ALLOWED_KEY_TYPES:
        raise SSHKeyError("Nieobslugiwany typ klucza SSH.")
    if not key_data:
        raise SSHKeyError("Brak danych base64 klucza SSH.")
    return key_type, key_data, comment


def _decode_key_data(key_data: str) -> bytes:
    padded = key_data + ("=" * (-len(key_data) % 4))
    try:
        raw = base64.b64decode(padded.encode("ascii"), validate=True)
    except Exception as exc:
        raise SSHKeyError("Nieprawidlowe dane base64 w kluczu SSH.") from exc
    if len(raw) < 16:
        raise SSHKeyError("Klucz SSH wydaje sie uszkodzony (zbyt krotkie dane).")
    return raw


def _sha256_fingerprint(raw_key: bytes) -> str:
    digest = hashlib.sha256(raw_key).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def _canonical_public_key(key_type: str, key_data: str, comment: str | None) -> str:
    return f"{key_type} {key_data}{' ' + comment if comment else ''}"


def client_ssh_dir(client: Client) -> Path:
    username = client.user.username if client.user is not None else f"client-{client.id}"
    root = Path(current_app.config.get("CLIENT_HOME_ROOT", "storage/clients")) / username
    ssh_dir = root / ".ssh"
    ssh_dir.mkdir(parents=True, exist_ok=True)
    return ssh_dir


def authorized_keys_file(client: Client) -> Path:
    return client_ssh_dir(client) / "authorized_keys"


def sync_client_authorized_keys(client: Client) -> Path:
    now = datetime.utcnow()
    keys = (
        ClientSSHKey.query.filter_by(client_id=client.id, status="active")
        .order_by(ClientSSHKey.created_at.asc(), ClientSSHKey.id.asc())
        .all()
    )
    lines = [f"{row.public_key.strip()}\n" for row in keys]

    target = authorized_keys_file(client)
    target.write_text("".join(lines), encoding="utf-8")

    for row in keys:
        row.last_installed_at = now
    db.session.flush()
    return target


def create_client_ssh_key(
    *,
    client: Client,
    public_key: str,
    label: str | None,
    created_by: User | None,
) -> ClientSSHKey:
    key_type, key_data, comment = _split_public_key(public_key)
    raw_key = _decode_key_data(key_data)
    fingerprint = _sha256_fingerprint(raw_key)
    canonical = _canonical_public_key(key_type, key_data, comment)

    existing = ClientSSHKey.query.filter_by(client_id=client.id, fingerprint_sha256=fingerprint).first()
    if existing is not None:
        raise SSHKeyError("Ten klucz SSH jest juz zapisany dla tego klienta.")

    safe_label = (label or "").strip()
    if not safe_label:
        safe_label = (comment or f"{key_type} {fingerprint[-10:]}").strip()

    row = ClientSSHKey(
        client=client,
        created_by=created_by,
        label=safe_label[:120],
        key_type=key_type,
        public_key=canonical,
        fingerprint_sha256=fingerprint,
        status="active",
        metadata_json={"comment": comment},
    )
    db.session.add(row)
    db.session.flush()
    sync_client_authorized_keys(client)
    return row


def set_client_ssh_key_status(*, key: ClientSSHKey, status: str) -> ClientSSHKey:
    normalized = (status or "").strip().lower()
    if normalized not in {"active", "disabled"}:
        raise SSHKeyError("Nieprawidlowy status klucza SSH.")
    key.status = normalized
    db.session.add(key)
    db.session.flush()
    sync_client_authorized_keys(key.client)
    return key


def delete_client_ssh_key(*, key: ClientSSHKey) -> None:
    client = key.client
    db.session.delete(key)
    db.session.flush()
    sync_client_authorized_keys(client)
