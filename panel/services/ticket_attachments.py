from __future__ import annotations

import os
import uuid
from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from panel.models import Ticket, TicketAttachment, TicketMessage, User


def _attachment_root() -> Path:
    root = Path(current_app.config.get("STORAGE_ROOT", "storage/uploads")) / "ticket_attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _max_bytes() -> int:
    return int(current_app.config.get("TICKETS_ATTACHMENT_MAX_BYTES", 5 * 1024 * 1024))


def _allowed_extensions() -> set[str]:
    raw = str(current_app.config.get("TICKETS_ATTACHMENT_ALLOWED_EXTENSIONS", "txt,pdf,png,jpg,jpeg,gif,zip,tar,gz,log,csv,json"))
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _extension(filename: str) -> str:
    _, ext = os.path.splitext(filename or "")
    return ext.lstrip(".").lower()


def save_ticket_attachment(
    *,
    ticket: Ticket,
    message: TicketMessage,
    uploaded_by: User,
    file_storage: FileStorage | None,
) -> TicketAttachment | None:
    if file_storage is None:
        return None
    if not getattr(file_storage, "filename", ""):
        return None

    original_name = secure_filename(file_storage.filename)
    if not original_name:
        raise ValueError("Nieprawidlowa nazwa pliku.")

    ext = _extension(original_name)
    if ext not in _allowed_extensions():
        raise ValueError("Niedozwolone rozszerzenie zalacznika.")

    content = file_storage.read() or b""
    size = len(content)
    if size <= 0:
        raise ValueError("Zalacznik jest pusty.")
    if size > _max_bytes():
        raise ValueError("Zalacznik przekracza maksymalny rozmiar.")

    ticket_dir = _attachment_root() / f"ticket_{ticket.id}"
    ticket_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}_{original_name}"
    target = ticket_dir / stored_name
    with target.open("wb") as fh:
        fh.write(content)

    relative_path = str(target.relative_to(_attachment_root()))
    return TicketAttachment(
        ticket=ticket,
        ticket_message=message,
        uploaded_by=uploaded_by,
        original_filename=original_name,
        storage_path=relative_path,
        mime_type=(file_storage.mimetype or "")[:120] or None,
        size_bytes=size,
    )


def resolve_attachment_path(attachment: TicketAttachment) -> Path:
    root = _attachment_root()
    candidate = (root / (attachment.storage_path or "")).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise ValueError("Nieprawidlowa sciezka zalacznika.")
    return candidate
