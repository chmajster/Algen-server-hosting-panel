from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_

from panel.extensions import db
from panel.models import (
    ActivityLog,
    AutomationExecution,
    Backup,
    BillingTransaction,
    Client,
    DataLegalHold,
    EventStreamEntry,
    RetentionCleanupRun,
    TenantRetentionPolicy,
    Ticket,
    TicketMessage,
    User,
    UserSession,
    WebhookDelivery,
    WebhookEndpoint,
)


RETENTION_RESOURCES = {
    "tickets": {"anonymize_after_days": 180, "archive_after_days": 90, "delete_after_days": 730},
    "invoices": {"anonymize_after_days": 365, "archive_after_days": 180, "delete_after_days": None},
    "logs": {"anonymize_after_days": 90, "archive_after_days": 60, "delete_after_days": 365},
    "sessions": {"anonymize_after_days": 30, "archive_after_days": 14, "delete_after_days": 90},
    "personal_data": {"anonymize_after_days": 365, "archive_after_days": 180, "delete_after_days": 730},
    "activity_logs_legacy": {"anonymize_after_days": 90, "delete_after_days": None},
    "webhook_deliveries": {"anonymize_after_days": 30, "delete_after_days": 180},
    "event_stream_entries": {"anonymize_after_days": 30, "delete_after_days": 120},
    "backups": {"anonymize_after_days": None, "delete_after_days": 365},
    "billing_transactions": {"anonymize_after_days": 365, "delete_after_days": None},
    "automation_executions": {"anonymize_after_days": 120, "delete_after_days": 365},
}


def retention_resource_choices() -> list[tuple[str, str]]:
    labels = {
        "tickets": "tickets",
        "invoices": "invoices",
        "logs": "logs",
        "sessions": "sessions",
        "personal_data": "personal_data",
        "activity_logs_legacy": "activity_logs_legacy",
        "webhook_deliveries": "webhook_deliveries",
        "event_stream_entries": "event_stream_entries",
        "backups": "backups",
        "billing_transactions": "billing_transactions",
        "automation_executions": "automation_executions",
    }
    return [(key, labels[key]) for key in RETENTION_RESOURCES.keys()]


def _to_non_negative_int(value: int | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return 0
    return parsed


def resolve_client_policy(client_id: int, resource_type: str) -> dict:
    resource = (resource_type or "").strip().lower()
    defaults = dict(RETENTION_RESOURCES.get(resource) or {})
    policy = TenantRetentionPolicy.query.filter_by(client_id=client_id, resource_type=resource).first()

    policy_archive_after_days = None
    if policy is not None and isinstance(policy.metadata_json, dict):
        policy_archive_after_days = _to_non_negative_int(policy.metadata_json.get("archive_after_days"))

    if policy is None:
        return {
            "resource_type": resource,
            "anonymize_after_days": defaults.get("anonymize_after_days"),
            "archive_after_days": defaults.get("archive_after_days"),
            "delete_after_days": defaults.get("delete_after_days"),
            "legal_hold_enabled": True,
            "is_active": True,
            "policy": None,
        }
    if not policy.is_active:
        return {
            "resource_type": resource,
            "anonymize_after_days": None,
            "archive_after_days": None,
            "delete_after_days": None,
            "legal_hold_enabled": bool(policy.legal_hold_enabled),
            "is_active": False,
            "policy": policy,
        }
    return {
        "resource_type": resource,
        "anonymize_after_days": _to_non_negative_int(policy.anonymize_after_days)
        if policy.anonymize_after_days is not None
        else defaults.get("anonymize_after_days"),
        "archive_after_days": policy_archive_after_days
        if policy_archive_after_days is not None
        else defaults.get("archive_after_days"),
        "delete_after_days": _to_non_negative_int(policy.delete_after_days)
        if policy.delete_after_days is not None
        else defaults.get("delete_after_days"),
        "legal_hold_enabled": bool(policy.legal_hold_enabled),
        "is_active": True,
        "policy": policy,
    }


def upsert_client_policy(
    *,
    client_id: int,
    resource_type: str,
    anonymize_after_days: int | None,
    archive_after_days: int | None = None,
    delete_after_days: int | None,
    legal_hold_enabled: bool,
    is_active: bool,
    notes: str | None,
) -> TenantRetentionPolicy:
    resource = (resource_type or "").strip().lower()
    row = TenantRetentionPolicy.query.filter_by(client_id=client_id, resource_type=resource).first()
    if row is None:
        row = TenantRetentionPolicy(client_id=client_id, resource_type=resource)
        db.session.add(row)

    row.anonymize_after_days = _to_non_negative_int(anonymize_after_days)
    row.delete_after_days = _to_non_negative_int(delete_after_days)
    row.legal_hold_enabled = bool(legal_hold_enabled)
    row.is_active = bool(is_active)
    row.notes = (notes or "").strip()[:255] or None
    metadata_json = dict(row.metadata_json or {})
    metadata_json["archive_after_days"] = _to_non_negative_int(archive_after_days)
    row.metadata_json = metadata_json
    return row


def _active_hold_query(*, resource_type: str, now: datetime):
    return (
        DataLegalHold.query.filter(DataLegalHold.status == "active")
        .filter(DataLegalHold.resource_type == resource_type)
        .filter(DataLegalHold.starts_at <= now)
        .filter(or_(DataLegalHold.expires_at.is_(None), DataLegalHold.expires_at > now))
        .filter(DataLegalHold.released_at.is_(None))
    )


def has_active_legal_hold(
    *,
    resource_type: str,
    resource_id: str | None,
    client_id: int | None,
    now: datetime | None = None,
) -> bool:
    current = now or datetime.utcnow()
    query = _active_hold_query(resource_type=resource_type, now=current)

    if client_id is None:
        query = query.filter(DataLegalHold.client_id.is_(None))
    else:
        query = query.filter(or_(DataLegalHold.client_id.is_(None), DataLegalHold.client_id == client_id))

    if resource_id:
        query = query.filter(or_(DataLegalHold.resource_id.is_(None), DataLegalHold.resource_id == resource_id))
    else:
        query = query.filter(DataLegalHold.resource_id.is_(None))

    return query.first() is not None


def create_legal_hold(
    *,
    client_id: int | None,
    resource_type: str,
    resource_id: str | None,
    reason: str,
    created_by: User | None,
    expires_at: datetime | None,
) -> DataLegalHold:
    row = DataLegalHold(
        client_id=client_id,
        resource_type=(resource_type or "").strip().lower(),
        resource_id=(resource_id or "").strip()[:120] or None,
        reason=(reason or "").strip()[:255] or "Legal hold",
        status="active",
        created_by=created_by,
        starts_at=datetime.utcnow(),
        expires_at=expires_at,
    )
    db.session.add(row)
    return row


def release_legal_hold(row: DataLegalHold) -> DataLegalHold:
    row.status = "released"
    row.released_at = datetime.utcnow()
    return row


def _apply_activity_log_legacy_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymized = 0
    skipped_holds = 0

    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is None:
        return {"scanned": 0, "anonymized": 0, "archived": 0, "deleted": 0, "skipped_holds": 0}

    cutoff = now - timedelta(days=max(0, int(anonymize_days)))
    query = ActivityLog.query.filter(ActivityLog.chain_legacy.is_(True)).filter(ActivityLog.created_at <= cutoff)
    if client_id is not None:
        query = query.filter(ActivityLog.client_id == client_id)

    rows = query.limit(5000).all()
    for row in rows:
        if policy.get("legal_hold_enabled") and has_active_legal_hold(
            resource_type="activity_logs_legacy",
            resource_id=str(row.id),
            client_id=row.client_id,
            now=now,
        ):
            skipped_holds += 1
            continue
        row.ip_address = None
        row.entity_id = None
        row.metadata_json = {}
        anonymized += 1

    return {"scanned": len(rows), "anonymized": anonymized, "archived": 0, "deleted": 0, "skipped_holds": skipped_holds}


def _apply_webhook_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymized = 0
    deleted = 0
    skipped_holds = 0
    scanned = 0

    anonymize_days = policy.get("anonymize_after_days")
    delete_days = policy.get("delete_after_days")

    if anonymize_days is not None:
        cutoff = now - timedelta(days=max(0, int(anonymize_days)))
        query = WebhookDelivery.query.join(WebhookEndpoint, WebhookDelivery.endpoint_id == WebhookEndpoint.id)
        query = query.filter(or_(WebhookDelivery.attempted_at <= cutoff, WebhookDelivery.created_at <= cutoff))
        if client_id is not None:
            query = query.filter(WebhookEndpoint.client_id == client_id)
        rows = query.limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="webhook_deliveries",
                resource_id=str(row.id),
                client_id=row.endpoint.client_id if row.endpoint else None,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.payload_json = None
            row.request_headers_json = None
            row.response_excerpt = None
            anonymized += 1

    if delete_days is not None:
        cutoff = now - timedelta(days=max(0, int(delete_days)))
        query = WebhookDelivery.query.join(WebhookEndpoint, WebhookDelivery.endpoint_id == WebhookEndpoint.id)
        query = query.filter(or_(WebhookDelivery.attempted_at <= cutoff, WebhookDelivery.created_at <= cutoff))
        if client_id is not None:
            query = query.filter(WebhookEndpoint.client_id == client_id)
        rows = query.limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="webhook_deliveries",
                resource_id=str(row.id),
                client_id=row.endpoint.client_id if row.endpoint else None,
                now=now,
            ):
                skipped_holds += 1
                continue
            db.session.delete(row)
            deleted += 1

    return {
        "scanned": scanned,
        "anonymized": anonymized,
        "archived": 0,
        "deleted": deleted,
        "skipped_holds": skipped_holds,
    }


def _apply_event_stream_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymized = 0
    deleted = 0
    skipped_holds = 0
    scanned = 0

    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is not None:
        cutoff = now - timedelta(days=max(0, int(anonymize_days)))
        query = EventStreamEntry.query.filter(EventStreamEntry.event_at <= cutoff)
        if client_id is not None:
            query = query.filter(EventStreamEntry.client_id == client_id)
        rows = query.limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="event_stream_entries",
                resource_id=str(row.id),
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.payload_json = None
            anonymized += 1

    delete_days = policy.get("delete_after_days")
    if delete_days is not None:
        cutoff = now - timedelta(days=max(0, int(delete_days)))
        query = EventStreamEntry.query.filter(EventStreamEntry.event_at <= cutoff)
        if client_id is not None:
            query = query.filter(EventStreamEntry.client_id == client_id)
        rows = query.limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="event_stream_entries",
                resource_id=str(row.id),
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            db.session.delete(row)
            deleted += 1

    return {
        "scanned": scanned,
        "anonymized": anonymized,
        "archived": 0,
        "deleted": deleted,
        "skipped_holds": skipped_holds,
    }


def _apply_backup_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    if client_id is None:
        return {"scanned": 0, "anonymized": 0, "archived": 0, "deleted": 0, "skipped_holds": 0}

    delete_days = policy.get("delete_after_days")
    if delete_days is None:
        return {"scanned": 0, "anonymized": 0, "archived": 0, "deleted": 0, "skipped_holds": 0}

    cutoff = now - timedelta(days=max(0, int(delete_days)))
    rows = (
        Backup.query.filter(Backup.client_id == client_id)
        .filter(Backup.created_at <= cutoff)
        .filter(Backup.status.in_(["completed", "failed", "deleted"]))
        .limit(5000)
        .all()
    )

    deleted = 0
    skipped_holds = 0
    for row in rows:
        if policy.get("legal_hold_enabled") and has_active_legal_hold(
            resource_type="backups",
            resource_id=str(row.id),
            client_id=row.client_id,
            now=now,
        ):
            skipped_holds += 1
            continue
        row.status = "deleted"
        row.external_location = None
        deleted += 1

    return {"scanned": len(rows), "anonymized": 0, "archived": 0, "deleted": deleted, "skipped_holds": skipped_holds}


def _apply_billing_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is None:
        return {"scanned": 0, "anonymized": 0, "archived": 0, "deleted": 0, "skipped_holds": 0}

    cutoff = now - timedelta(days=max(0, int(anonymize_days)))
    query = BillingTransaction.query.filter(BillingTransaction.created_at <= cutoff)
    if client_id is not None:
        query = query.filter(BillingTransaction.client_id == client_id)

    rows = query.limit(5000).all()
    anonymized = 0
    skipped_holds = 0
    for row in rows:
        if policy.get("legal_hold_enabled") and has_active_legal_hold(
            resource_type="billing_transactions",
            resource_id=str(row.id),
            client_id=row.client_id,
            now=now,
        ):
            skipped_holds += 1
            continue
        row.metadata_json = {}
        anonymized += 1

    return {"scanned": len(rows), "anonymized": anonymized, "archived": 0, "deleted": 0, "skipped_holds": skipped_holds}


def _apply_automation_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    if client_id is not None:
        return {"scanned": 0, "anonymized": 0, "archived": 0, "deleted": 0, "skipped_holds": 0}

    anonymize = 0
    deleted = 0
    skipped_holds = 0
    scanned = 0

    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is not None:
        cutoff = now - timedelta(days=max(0, int(anonymize_days)))
        rows = AutomationExecution.query.filter(AutomationExecution.created_at <= cutoff).limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="automation_executions",
                resource_id=str(row.id),
                client_id=None,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.metadata_json = {}
            anonymize += 1

    delete_days = policy.get("delete_after_days")
    if delete_days is not None:
        cutoff = now - timedelta(days=max(0, int(delete_days)))
        rows = AutomationExecution.query.filter(AutomationExecution.created_at <= cutoff).limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="automation_executions",
                resource_id=str(row.id),
                client_id=None,
                now=now,
            ):
                skipped_holds += 1
                continue
            db.session.delete(row)
            deleted += 1

    return {
        "scanned": scanned,
        "anonymized": anonymize,
        "archived": 0,
        "deleted": deleted,
        "skipped_holds": skipped_holds,
    }


def _apply_ticket_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymized = 0
    archived = 0
    deleted = 0
    skipped_holds = 0
    scanned = 0

    query = Ticket.query
    if client_id is not None:
        query = query.filter(Ticket.client_id == client_id)

    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is not None:
        cutoff = now - timedelta(days=max(0, int(anonymize_days)))
        rows = query.filter(Ticket.created_at <= cutoff).limit(2000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="tickets",
                resource_id=str(row.id),
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.subject = "[anonymized ticket]"
            row.metadata_json = {**(row.metadata_json or {}), "retention_anonymized_at": now.isoformat()}
            for message_row in TicketMessage.query.filter(TicketMessage.ticket_id == row.id).all():
                message_row.message = "[anonymized]"
                message_row.metadata_json = {
                    **(message_row.metadata_json or {}),
                    "retention_anonymized_at": now.isoformat(),
                }
            anonymized += 1

    archive_days = policy.get("archive_after_days")
    if archive_days is not None:
        cutoff = now - timedelta(days=max(0, int(archive_days)))
        rows = query.filter(Ticket.created_at <= cutoff).limit(2000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="tickets",
                resource_id=str(row.id),
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            metadata_json = dict(row.metadata_json or {})
            if metadata_json.get("retention_archived_at"):
                continue
            metadata_json["retention_archived_at"] = now.isoformat()
            row.metadata_json = metadata_json
            archived += 1

    delete_days = policy.get("delete_after_days")
    if delete_days is not None:
        cutoff = now - timedelta(days=max(0, int(delete_days)))
        rows = query.filter(Ticket.created_at <= cutoff).limit(2000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="tickets",
                resource_id=str(row.id),
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            db.session.delete(row)
            deleted += 1

    return {
        "scanned": scanned,
        "anonymized": anonymized,
        "archived": archived,
        "deleted": deleted,
        "skipped_holds": skipped_holds,
    }


def _apply_invoice_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymized = 0
    archived = 0
    deleted = 0
    skipped_holds = 0
    scanned = 0

    query = BillingTransaction.query
    if client_id is not None:
        query = query.filter(BillingTransaction.client_id == client_id)

    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is not None:
        cutoff = now - timedelta(days=max(0, int(anonymize_days)))
        rows = query.filter(BillingTransaction.created_at <= cutoff).limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="invoices",
                resource_id=str(row.id),
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.description = "[anonymized invoice entry]"
            row.metadata_json = {}
            anonymized += 1

    archive_days = policy.get("archive_after_days")
    if archive_days is not None:
        cutoff = now - timedelta(days=max(0, int(archive_days)))
        rows = query.filter(BillingTransaction.created_at <= cutoff).limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="invoices",
                resource_id=str(row.id),
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            metadata_json = dict(row.metadata_json or {})
            if metadata_json.get("retention_archived_at"):
                continue
            metadata_json["retention_archived_at"] = now.isoformat()
            row.metadata_json = metadata_json
            archived += 1

    delete_days = policy.get("delete_after_days")
    if delete_days is not None:
        cutoff = now - timedelta(days=max(0, int(delete_days)))
        rows = query.filter(BillingTransaction.created_at <= cutoff).limit(5000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="invoices",
                resource_id=str(row.id),
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            db.session.delete(row)
            deleted += 1

    return {
        "scanned": scanned,
        "anonymized": anonymized,
        "archived": archived,
        "deleted": deleted,
        "skipped_holds": skipped_holds,
    }


def _apply_logs_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymized = 0
    archived = 0
    deleted = 0
    skipped_holds = 0
    scanned = 0

    activity_query = ActivityLog.query
    events_query = EventStreamEntry.query
    if client_id is not None:
        activity_query = activity_query.filter(ActivityLog.client_id == client_id)
        events_query = events_query.filter(EventStreamEntry.client_id == client_id)

    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is not None:
        cutoff = now - timedelta(days=max(0, int(anonymize_days)))
        activity_rows = activity_query.filter(ActivityLog.created_at <= cutoff).limit(4000).all()
        scanned += len(activity_rows)
        for row in activity_rows:
            hold_id = f"activity:{row.id}"
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="logs",
                resource_id=hold_id,
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.ip_address = None
            row.entity_id = None
            row.metadata_json = {}
            anonymized += 1

        event_rows = events_query.filter(EventStreamEntry.event_at <= cutoff).limit(4000).all()
        scanned += len(event_rows)
        for row in event_rows:
            hold_id = f"event:{row.id}"
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="logs",
                resource_id=hold_id,
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.payload_json = {}
            anonymized += 1

    archive_days = policy.get("archive_after_days")
    if archive_days is not None:
        cutoff = now - timedelta(days=max(0, int(archive_days)))
        activity_rows = activity_query.filter(ActivityLog.created_at <= cutoff).limit(4000).all()
        scanned += len(activity_rows)
        for row in activity_rows:
            hold_id = f"activity:{row.id}"
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="logs",
                resource_id=hold_id,
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            metadata_json = dict(row.metadata_json or {})
            if metadata_json.get("retention_archived_at"):
                continue
            metadata_json["retention_archived_at"] = now.isoformat()
            row.metadata_json = metadata_json
            archived += 1

    delete_days = policy.get("delete_after_days")
    if delete_days is not None:
        cutoff = now - timedelta(days=max(0, int(delete_days)))
        event_rows = events_query.filter(EventStreamEntry.event_at <= cutoff).limit(4000).all()
        scanned += len(event_rows)
        for row in event_rows:
            hold_id = f"event:{row.id}"
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="logs",
                resource_id=hold_id,
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            db.session.delete(row)
            deleted += 1

        activity_rows = (
            activity_query.filter(ActivityLog.created_at <= cutoff)
            .filter(ActivityLog.chain_legacy.is_(True))
            .limit(4000)
            .all()
        )
        scanned += len(activity_rows)
        for row in activity_rows:
            hold_id = f"activity:{row.id}"
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="logs",
                resource_id=hold_id,
                client_id=row.client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            db.session.delete(row)
            deleted += 1

    return {
        "scanned": scanned,
        "anonymized": anonymized,
        "archived": archived,
        "deleted": deleted,
        "skipped_holds": skipped_holds,
    }


def _apply_session_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymized = 0
    archived = 0
    deleted = 0
    skipped_holds = 0
    scanned = 0

    query = UserSession.query.join(User, UserSession.user_id == User.id).join(Client, Client.user_id == User.id)
    if client_id is not None:
        query = query.filter(Client.id == client_id)

    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is not None:
        cutoff = now - timedelta(days=max(0, int(anonymize_days)))
        rows = query.filter(UserSession.last_activity_at <= cutoff).limit(4000).all()
        scanned += len(rows)
        for row in rows:
            tenant_client_id = row.user.client_profile.id if row.user and row.user.client_profile else None
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="sessions",
                resource_id=str(row.id),
                client_id=tenant_client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.ip_address = None
            row.user_agent = None
            anonymized += 1

    archive_days = policy.get("archive_after_days")
    if archive_days is not None:
        cutoff = now - timedelta(days=max(0, int(archive_days)))
        rows = query.filter(UserSession.last_activity_at <= cutoff).limit(4000).all()
        scanned += len(rows)
        for row in rows:
            tenant_client_id = row.user.client_profile.id if row.user and row.user.client_profile else None
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="sessions",
                resource_id=str(row.id),
                client_id=tenant_client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            if row.revoked_at is not None:
                continue
            row.revoked_at = now
            archived += 1

    delete_days = policy.get("delete_after_days")
    if delete_days is not None:
        cutoff = now - timedelta(days=max(0, int(delete_days)))
        rows = query.filter(UserSession.last_activity_at <= cutoff).limit(4000).all()
        scanned += len(rows)
        for row in rows:
            tenant_client_id = row.user.client_profile.id if row.user and row.user.client_profile else None
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="sessions",
                resource_id=str(row.id),
                client_id=tenant_client_id,
                now=now,
            ):
                skipped_holds += 1
                continue
            db.session.delete(row)
            deleted += 1

    return {
        "scanned": scanned,
        "anonymized": anonymized,
        "archived": archived,
        "deleted": deleted,
        "skipped_holds": skipped_holds,
    }


def _apply_personal_data_retention(*, client_id: int | None, policy: dict, now: datetime) -> dict[str, int]:
    anonymized = 0
    archived = 0
    deleted = 0
    skipped_holds = 0
    scanned = 0

    query = Client.query
    if client_id is not None:
        query = query.filter(Client.id == client_id)

    anonymize_days = policy.get("anonymize_after_days")
    if anonymize_days is not None:
        cutoff = now - timedelta(days=max(0, int(anonymize_days)))
        rows = query.filter(Client.created_at <= cutoff).limit(1000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="personal_data",
                resource_id=str(row.id),
                client_id=row.id,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.company_name = None
            row.phone = None
            row.address = None
            row.city = None
            row.country = None
            if row.user is not None:
                row.user.first_name = "Anon"
                row.user.last_name = f"Client{row.id}"
                row.user.last_login_ip = None
            anonymized += 1

    archive_days = policy.get("archive_after_days")
    if archive_days is not None:
        cutoff = now - timedelta(days=max(0, int(archive_days)))
        rows = query.filter(Client.created_at <= cutoff).limit(1000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="personal_data",
                resource_id=str(row.id),
                client_id=row.id,
                now=now,
            ):
                skipped_holds += 1
                continue
            note = (row.notes or "").strip()
            if "[retention-archived]" in note:
                continue
            row.notes = f"{note} [retention-archived {now.strftime('%Y-%m-%d')}]".strip()
            archived += 1

    delete_days = policy.get("delete_after_days")
    if delete_days is not None:
        cutoff = now - timedelta(days=max(0, int(delete_days)))
        rows = query.filter(Client.created_at <= cutoff).limit(1000).all()
        scanned += len(rows)
        for row in rows:
            if policy.get("legal_hold_enabled") and has_active_legal_hold(
                resource_type="personal_data",
                resource_id=str(row.id),
                client_id=row.id,
                now=now,
            ):
                skipped_holds += 1
                continue
            row.company_name = None
            row.phone = None
            row.address = None
            row.city = None
            row.country = None
            if row.user is not None:
                row.user.email = f"deleted+{row.user.id}@example.invalid"
                row.user.first_name = "Deleted"
                row.user.last_name = f"User{row.user.id}"
                row.user.last_login_ip = None
            deleted += 1

    return {
        "scanned": scanned,
        "anonymized": anonymized,
        "archived": archived,
        "deleted": deleted,
        "skipped_holds": skipped_holds,
    }


def _resource_handler(resource_type: str):
    mapping = {
        "tickets": _apply_ticket_retention,
        "invoices": _apply_invoice_retention,
        "logs": _apply_logs_retention,
        "sessions": _apply_session_retention,
        "personal_data": _apply_personal_data_retention,
        "activity_logs_legacy": _apply_activity_log_legacy_retention,
        "webhook_deliveries": _apply_webhook_retention,
        "event_stream_entries": _apply_event_stream_retention,
        "backups": _apply_backup_retention,
        "billing_transactions": _apply_billing_retention,
        "automation_executions": _apply_automation_retention,
    }
    return mapping.get(resource_type)


def _all_client_ids() -> list[int]:
    return [row.id for row in Client.query.order_by(Client.id.asc()).all()]


def run_retention_cleanup(
    *,
    run_key: str | None = None,
    triggered_by: User | None = None,
    client_id: int | None = None,
) -> dict:
    normalized_key = (run_key or "").strip()[:120] or None
    if normalized_key:
        existing = RetentionCleanupRun.query.filter_by(run_key=normalized_key).first()
        if existing is not None and existing.status == "completed":
            return {
                "run_id": existing.id,
                "status": existing.status,
                "idempotent": True,
                "summary": existing.summary_json or {},
            }

    run = RetentionCleanupRun(
        run_key=normalized_key,
        status="running",
        started_at=datetime.utcnow(),
        triggered_by=triggered_by,
        summary_json={},
    )
    db.session.add(run)
    db.session.flush()

    now = datetime.utcnow()
    client_ids = [client_id] if client_id is not None else _all_client_ids()
    scoped_client_ids = list(dict.fromkeys([cid for cid in client_ids if cid is not None]))

    by_client: dict[str, dict] = {}
    totals: dict[str, dict] = {
        resource: {"scanned": 0, "anonymized": 0, "archived": 0, "deleted": 0, "skipped_holds": 0}
        for resource in RETENTION_RESOURCES.keys()
    }

    for cid in scoped_client_ids:
        client_summary: dict[str, dict] = {}
        for resource in RETENTION_RESOURCES.keys():
            policy = resolve_client_policy(cid, resource)
            handler = _resource_handler(resource)
            if handler is None or not policy.get("is_active"):
                client_summary[resource] = {
                    "scanned": 0,
                    "anonymized": 0,
                    "archived": 0,
                    "deleted": 0,
                    "skipped_holds": 0,
                }
                continue
            result = handler(client_id=cid, policy=policy, now=now)
            client_summary[resource] = result
            for key in totals[resource].keys():
                totals[resource][key] += int(result.get(key, 0) or 0)
        by_client[str(cid)] = client_summary

    # Global resources without tenant ownership are processed once in global scope.
    for resource in ["automation_executions"]:
        handler = _resource_handler(resource)
        if handler is None:
            continue
        policy = {
            "anonymize_after_days": RETENTION_RESOURCES[resource].get("anonymize_after_days"),
            "archive_after_days": RETENTION_RESOURCES[resource].get("archive_after_days"),
            "delete_after_days": RETENTION_RESOURCES[resource].get("delete_after_days"),
            "legal_hold_enabled": True,
            "is_active": True,
        }
        result = handler(client_id=None, policy=policy, now=now)
        for key in totals[resource].keys():
            totals[resource][key] += int(result.get(key, 0) or 0)

    run.status = "completed"
    run.finished_at = datetime.utcnow()
    run.summary_json = {
        "by_client": by_client,
        "totals": totals,
        "clients_processed": len(scoped_client_ids),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }

    return {
        "run_id": run.id,
        "status": run.status,
        "idempotent": False,
        "summary": run.summary_json,
    }
