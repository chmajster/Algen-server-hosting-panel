from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from flask import current_app
from flask import has_request_context, request
from flask_login import current_user

from panel.extensions import db
from panel.models import ActivityLog, Client, User


AUDIT_CHAIN_VERSION = "v1"
AUDIT_CHAIN_GENESIS = "GENESIS"


def _chain_enabled() -> bool:
    try:
        return bool(current_app.config.get("AUDIT_CHAIN_ENABLED", True))
    except RuntimeError:
        return True


def _chain_secret() -> str:
    try:
        return str(current_app.config.get("AUDIT_CHAIN_SECRET", ""))
    except RuntimeError:
        return ""


def _normalize_metadata(metadata: dict | None) -> Any:
    if metadata is None:
        return {}
    if isinstance(metadata, (dict, list, str, int, float, bool)):
        return metadata
    return {"value": str(metadata)}


def _serialize_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)


def _payload_hash(payload: dict[str, Any]) -> str:
    serialized = _serialize_payload(payload)
    to_hash = f"{_chain_secret()}|{serialized}".encode("utf-8")
    return hashlib.sha256(to_hash).hexdigest()


def _latest_chain_log() -> ActivityLog | None:
    return (
        ActivityLog.query.filter(ActivityLog.chain_hash.isnot(None))
        .order_by(ActivityLog.chain_sequence.desc(), ActivityLog.id.desc())
        .first()
    )


def _chain_payload(
    *,
    created_at: datetime,
    action: str,
    entity_type: str,
    entity_id: str | None,
    description: str,
    actor_user_id: int | None,
    client_id: int | None,
    ip_address: str | None,
    success: bool,
    metadata_json: Any,
    sequence: int,
    previous_hash: str,
) -> dict[str, Any]:
    return {
        "version": AUDIT_CHAIN_VERSION,
        "sequence": sequence,
        "previous_hash": previous_hash,
        "created_at": created_at.isoformat(timespec="microseconds"),
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "description": description,
        "actor_user_id": actor_user_id,
        "client_id": client_id,
        "ip_address": ip_address,
        "success": bool(success),
        "metadata": metadata_json,
    }


def _event_category_for_action(action: str) -> str:
    value = (action or "").strip().lower()
    if value.startswith(("auth.", "approvals.", "api_tokens.", "ssh.", "governance.secret")):
        return "security"
    if value.startswith(("backup.", "backups.")):
        return "backups"
    if value.startswith(("webhook.", "webhooks.")):
        return "webhooks"
    if value.startswith("billing."):
        return "billing"
    if value.startswith(("tickets.", "ticket.")):
        return "tickets"
    if value.startswith("automation."):
        return "automation"
    if value.startswith(("governance.", "compliance.")):
        return "compliance"
    return "system"


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

    metadata_json = _normalize_metadata(metadata)
    ip_address = None
    if has_request_context():
        ip_address = request.headers.get("X-Forwarded-For", request.remote_addr)

    created_at = datetime.utcnow()
    chain_sequence: int | None = None
    chain_prev_hash: str | None = None
    chain_hash: str | None = None
    chain_version: str | None = None
    chain_legacy = not _chain_enabled()

    if not chain_legacy:
        previous = _latest_chain_log()
        chain_sequence = (previous.chain_sequence or 0) + 1 if previous is not None else 1
        chain_prev_hash = previous.chain_hash if previous is not None else AUDIT_CHAIN_GENESIS
        chain_version = AUDIT_CHAIN_VERSION
        payload = _chain_payload(
            created_at=created_at,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            description=description,
            actor_user_id=actor_obj.id if actor_obj is not None else None,
            client_id=client.id if client is not None else None,
            ip_address=ip_address,
            success=success,
            metadata_json=metadata_json,
            sequence=chain_sequence,
            previous_hash=chain_prev_hash,
        )
        chain_hash = _payload_hash(payload)

    log = ActivityLog(
        actor=actor_obj,
        client=client,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        description=description,
        ip_address=ip_address,
        success=success,
        metadata_json=metadata_json,
        created_at=created_at,
        updated_at=created_at,
        chain_sequence=chain_sequence,
        chain_prev_hash=chain_prev_hash,
        chain_hash=chain_hash,
        chain_version=chain_version,
        chain_legacy=chain_legacy,
    )
    db.session.add(log)

    try:
        from panel.services.event_stream import emit_event

        emit_event(
            event_type=f"activity.{action}",
            message=description,
            category=_event_category_for_action(action),
            severity="info" if success else "warning",
            source="audit",
            client=client,
            actor=actor_obj,
            payload={
                "activity_id": log.id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": str(entity_id) if entity_id is not None else None,
                "success": bool(success),
            },
        )
    except Exception:
        # Event-stream emission must not block audit persistence.
        pass


def verify_activity_chain(*, max_errors: int = 100) -> dict[str, Any]:
    logs = (
        ActivityLog.query.filter(ActivityLog.chain_legacy.is_(False))
        .filter(ActivityLog.chain_hash.isnot(None))
        .order_by(ActivityLog.chain_sequence.asc(), ActivityLog.id.asc())
        .all()
    )
    errors: list[dict[str, Any]] = []
    previous: ActivityLog | None = None

    for row in logs:
        if previous is None:
            expected_sequence = 1
            expected_prev_hash = AUDIT_CHAIN_GENESIS
        else:
            expected_sequence = (previous.chain_sequence or 0) + 1
            expected_prev_hash = previous.chain_hash or AUDIT_CHAIN_GENESIS

        if row.chain_sequence != expected_sequence:
            errors.append(
                {
                    "id": row.id,
                    "type": "sequence_mismatch",
                    "expected": expected_sequence,
                    "actual": row.chain_sequence,
                }
            )

        if row.chain_prev_hash != expected_prev_hash:
            errors.append(
                {
                    "id": row.id,
                    "type": "prev_hash_mismatch",
                    "expected": expected_prev_hash,
                    "actual": row.chain_prev_hash,
                }
            )

        payload = _chain_payload(
            created_at=row.created_at,
            action=row.action,
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            description=row.description,
            actor_user_id=row.actor_user_id,
            client_id=row.client_id,
            ip_address=row.ip_address,
            success=bool(row.success),
            metadata_json=row.metadata_json,
            sequence=row.chain_sequence or 0,
            previous_hash=row.chain_prev_hash or AUDIT_CHAIN_GENESIS,
        )
        expected_hash = _payload_hash(payload)
        if row.chain_hash != expected_hash:
            errors.append(
                {
                    "id": row.id,
                    "type": "hash_mismatch",
                    "expected": expected_hash,
                    "actual": row.chain_hash,
                }
            )

        previous = row
        if len(errors) >= max_errors:
            break

    return {
        "checked": len(logs),
        "errors": errors,
        "valid": len(errors) == 0,
        "legacy_rows": ActivityLog.query.filter(ActivityLog.chain_legacy.is_(True)).count(),
        "latest_sequence": previous.chain_sequence if previous is not None else 0,
        "latest_hash": previous.chain_hash if previous is not None else None,
    }
