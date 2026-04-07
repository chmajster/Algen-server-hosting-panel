from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Iterator

from sqlalchemy import or_

from panel.extensions import db
from panel.models import Client, EventStreamEntry, User


EVENT_CATEGORIES = {
    "security",
    "billing",
    "tickets",
    "backups",
    "webhooks",
    "automation",
    "infrastructure",
    "compliance",
    "policy",
    "system",
}
EVENT_SEVERITIES = {"debug", "info", "warning", "error", "critical"}
SENSITIVE_PAYLOAD_KEYS = {
    "password",
    "secret",
    "token",
    "api_key",
    "private_key",
    "authorization",
    "cookie",
    "access_token",
    "refresh_token",
}


def _normalized_text(value: str | None) -> str:
    return (value or "").strip()


def normalize_category(value: str | None, *, default: str = "system") -> str:
    candidate = _normalized_text(value).lower()
    if candidate in EVENT_CATEGORIES:
        return candidate
    return default


def normalize_severity(value: str | None, *, default: str = "info") -> str:
    candidate = _normalized_text(value).lower()
    if candidate in EVENT_SEVERITIES:
        return candidate
    return default


def _sanitize_payload(value):
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in SENSITIVE_PAYLOAD_KEYS:
                sanitized[key] = "[redacted]"
            else:
                sanitized[key] = _sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    return value


def emit_event(
    event_type: str,
    message: str,
    *,
    category: str = "system",
    severity: str = "info",
    source: str = "application",
    client: Client | None = None,
    actor: User | None = None,
    payload: dict | None = None,
    fingerprint: str | None = None,
    reference_id: str | int | None = None,
    reference_type: str | None = None,
    event_at: datetime | None = None,
) -> EventStreamEntry:
    payload_json = _sanitize_payload(payload or {})
    if reference_id is not None and "reference_id" not in payload_json:
        payload_json["reference_id"] = str(reference_id)
    if reference_type is not None and "reference_type" not in payload_json:
        payload_json["reference_type"] = str(reference_type)[:64]

    entry = EventStreamEntry(
        event_type=_normalized_text(event_type)[:120] or "system.event",
        category=normalize_category(category),
        severity=normalize_severity(severity),
        source=_normalized_text(source)[:64] or "application",
        message=_normalized_text(message)[:255] or "System event",
        client=client,
        actor=actor,
        payload_json=payload_json,
        event_fingerprint=_normalized_text(fingerprint)[:64] or None,
        event_at=event_at or datetime.utcnow(),
    )
    db.session.add(entry)
    return entry


def event_to_dict(entry: EventStreamEntry) -> dict:
    payload_json = dict(entry.payload_json or {})
    reference_id = payload_json.get("reference_id")
    reference_type = payload_json.get("reference_type")
    return {
        "id": entry.id,
        "type": entry.event_type,
        "tenant": entry.client_id,
        "actor": entry.actor_user_id,
        "timestamp": entry.event_at.isoformat() if entry.event_at else None,
        "reference_id": str(reference_id) if reference_id is not None else None,
        "reference_type": str(reference_type) if reference_type is not None else None,
        "event_type": entry.event_type,
        "category": entry.category,
        "severity": entry.severity,
        "source": entry.source,
        "message": entry.message,
        "client_id": entry.client_id,
        "actor_user_id": entry.actor_user_id,
        "event_at": entry.event_at.isoformat() if entry.event_at else None,
        "payload": payload_json,
    }


def query_events(
    *,
    client_id: int | None = None,
    category: str | None = None,
    severity: str | None = None,
    event_type: str | None = None,
    search: str | None = None,
    min_id: int | None = None,
    limit: int = 100,
) -> list[EventStreamEntry]:
    query = EventStreamEntry.query
    if client_id is not None:
        query = query.filter(or_(EventStreamEntry.client_id.is_(None), EventStreamEntry.client_id == client_id))
    if category:
        query = query.filter(EventStreamEntry.category == normalize_category(category, default=""))
    if severity:
        query = query.filter(EventStreamEntry.severity == normalize_severity(severity, default=""))
    if event_type:
        query = query.filter(EventStreamEntry.event_type == _normalized_text(event_type)[:120])
    if search:
        like_value = f"%{_normalized_text(search)}%"
        query = query.filter(
            or_(
                EventStreamEntry.message.ilike(like_value),
                EventStreamEntry.event_type.ilike(like_value),
                EventStreamEntry.source.ilike(like_value),
            )
        )
    if min_id is not None:
        query = query.filter(EventStreamEntry.id > max(0, int(min_id)))

    return query.order_by(EventStreamEntry.id.desc()).limit(max(1, min(500, int(limit)))).all()


def iter_sse_events(
    *,
    last_id: int,
    client_id: int | None,
    category: str | None,
    severity: str | None,
    event_type: str | None,
    search: str | None,
    max_cycles: int = 30,
    poll_seconds: float = 1.0,
) -> Iterator[str]:
    current_last_id = max(0, int(last_id))
    yield "retry: 5000\n\n"

    for _ in range(max(1, int(max_cycles))):
        rows = query_events(
            client_id=client_id,
            category=category,
            severity=severity,
            event_type=event_type,
            search=search,
            min_id=current_last_id,
            limit=200,
        )
        ordered_rows = list(reversed(rows))
        for row in ordered_rows:
            current_last_id = max(current_last_id, int(row.id or 0))
            data = json.dumps(event_to_dict(row), ensure_ascii=True)
            yield f"id: {current_last_id}\n"
            yield f"event: {row.category}\n"
            yield f"data: {data}\n\n"
        time.sleep(max(0.2, float(poll_seconds)))

    yield "event: heartbeat\n"
    yield "data: {}\n\n"
