from __future__ import annotations

from flask import has_request_context, request
from flask_login import current_user

from panel.extensions import db
from panel.models import ActivityLog, Client, User


def log_activity(
    action: str,
    entity_type: str,
    description: str,
    *,
    entity_id: str | int | None = None,
    client: Client | None = None,
    actor: User | None = None,
    metadata: dict | None = None,
    success: bool = True,
) -> None:
    actor_obj = actor
    if actor_obj is None and has_request_context() and getattr(current_user, "is_authenticated", False):
        actor_obj = current_user
    ip_address = None
    if has_request_context():
        ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)
    log = ActivityLog(
        actor=actor_obj,
        client=client,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        description=description,
        ip_address=ip_address,
        success=success,
        metadata_json=metadata or {},
    )
    db.session.add(log)
