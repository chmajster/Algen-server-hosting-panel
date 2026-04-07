from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from flask import current_app

from panel.models import (
    Client,
    ClientResourceSample,
    ClientService,
    HostingDatabase,
    Mailbox,
    ResourceLimitAlert,
)
from panel.services.audit import log_activity
from panel.services.mailer import send_plain_email


RESOURCE_DEFINITIONS = {
    "disk_mb": {
        "label": "Dysk",
        "soft_keys": ["disk_soft_mb"],
        "hard_keys": ["disk_hard_mb", "disk_mb"],
        "unit": "MB",
    },
    "inode_count": {
        "label": "Inody",
        "soft_keys": ["inode_soft", "inodes_soft"],
        "hard_keys": ["inode_hard", "inodes_hard", "inodes", "inode_limit"],
        "unit": "inodes",
    },
    "database_count": {
        "label": "Bazy danych",
        "soft_keys": ["databases_soft"],
        "hard_keys": ["databases_hard", "databases"],
        "unit": "count",
    },
    "mailbox_count": {
        "label": "Skrzynki e-mail",
        "soft_keys": ["mailboxes_soft"],
        "hard_keys": ["mailboxes_hard", "mailboxes"],
        "unit": "count",
    },
}


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    return parsed


def _collect_plan_limits(client: Client) -> dict:
    service = (
        ClientService.query.filter_by(client_id=client.id, service_type="hosting")
        .filter(ClientService.status != "deleted")
        .order_by(ClientService.created_at.desc())
        .first()
    )
    if service is None or service.plan is None:
        return {}
    return dict(service.plan.limits_json or {})


def resolve_client_resource_limits(client: Client) -> dict:
    plan_limits = _collect_plan_limits(client)
    account_limits = dict(client.resource_limits or {})
    merged = {**plan_limits, **account_limits}
    return merged


def _latest_sample(client_id: int) -> ClientResourceSample | None:
    return (
        ClientResourceSample.query.filter_by(client_id=client_id)
        .order_by(ClientResourceSample.created_at.desc())
        .first()
    )


def _resource_usage_value(client: Client, key: str, sample: ClientResourceSample | None) -> float | None:
    if key == "database_count":
        return float(HostingDatabase.query.filter_by(client_id=client.id).count())
    if key == "mailbox_count":
        return float(Mailbox.query.filter_by(client_id=client.id).count())
    if sample is None:
        return None
    raw = getattr(sample, key, None)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _resolve_limit_value(raw_limits: dict, keys: list[str]) -> float | None:
    for key in keys:
        parsed = _to_float(raw_limits.get(key))
        if parsed is not None:
            return parsed
    return None


def _resolve_status(usage: float | None, soft_limit: float | None, hard_limit: float | None) -> tuple[str, float | None]:
    if usage is None:
        return "unknown", None
    if hard_limit is not None and hard_limit > 0:
        percent = round((usage / hard_limit) * 100, 2)
    else:
        percent = None

    if hard_limit is not None and usage >= hard_limit:
        return "hard_exceeded", percent
    if soft_limit is not None and usage >= soft_limit:
        return "soft_exceeded", percent
    if percent is not None and percent >= 95:
        return "warn95", percent
    if percent is not None and percent >= 80:
        return "warn80", percent
    return "ok", percent


def resource_usage_report(client: Client) -> dict:
    limits = resolve_client_resource_limits(client)
    sample = _latest_sample(client.id)
    measured_at = sample.created_at if sample is not None else None

    report: dict[str, dict] = {}
    for key, definition in RESOURCE_DEFINITIONS.items():
        usage = _resource_usage_value(client, key, sample)
        soft_limit = _resolve_limit_value(limits, definition["soft_keys"])
        hard_limit = _resolve_limit_value(limits, definition["hard_keys"])
        status, percent = _resolve_status(usage, soft_limit, hard_limit)
        report[key] = {
            "resource_key": key,
            "label": definition["label"],
            "usage": usage,
            "soft_limit": soft_limit,
            "hard_limit": hard_limit,
            "percent": percent,
            "status": status,
            "unit": definition["unit"],
            "last_measured_at": measured_at,
        }
    return report


def _notify_resource_alert(client: Client, alert: ResourceLimitAlert) -> None:
    channels: list[str] = []
    if bool(current_app.config.get("RESOURCE_ALERT_WEBHOOK_ENABLED", True)):
        try:
            from panel.services.webhooks import dispatch_webhook_event

            dispatch_webhook_event(
                "resource.limit_alert",
                {
                    "client_id": client.id,
                    "username": client.user.username if client.user else None,
                    "resource_key": alert.resource_key,
                    "threshold": alert.threshold_label,
                    "usage": str(alert.usage_value) if alert.usage_value is not None else None,
                    "limit": str(alert.limit_value) if alert.limit_value is not None else None,
                    "message": alert.message,
                },
                client=client,
                auto_commit=False,
            )
            channels.append("webhook")
        except Exception:
            pass

    if bool(current_app.config.get("RESOURCE_ALERT_EMAIL_ENABLED", True)):
        recipients: list[str] = []
        if client.user and client.user.email:
            recipients.append(client.user.email)
        admin_email = str(current_app.config.get("RESOURCE_ALERT_ADMIN_EMAIL", "") or "").strip()
        if admin_email:
            recipients.append(admin_email)

        for recipient in list(dict.fromkeys(recipients)):
            subject = f"[Hosting Panel] Alert limitu zasobow: {alert.resource_key}"
            body = (
                f"Klient: {client.user.username if client.user else client.id}\n"
                f"Zasob: {alert.resource_key}\n"
                f"Prog: {alert.threshold_label}\n"
                f"Uzycie: {alert.usage_value}\n"
                f"Limit: {alert.limit_value}\n"
                f"Komunikat: {alert.message}\n"
            )
            error = send_plain_email(to_email=recipient, subject=subject, body=body)
            if error is None:
                channels.append("email")

    alert.notification_channels_json = sorted(set(channels))


def _active_alert(client_id: int, resource_key: str, threshold_label: str) -> ResourceLimitAlert | None:
    return (
        ResourceLimitAlert.query.filter_by(
            client_id=client_id,
            resource_key=resource_key,
            threshold_label=threshold_label,
            status="active",
        )
        .order_by(ResourceLimitAlert.id.desc())
        .first()
    )


def _trigger_alert(client: Client, metric: dict, threshold_label: str, threshold_percent: int | None) -> None:
    existing = _active_alert(client.id, metric["resource_key"], threshold_label)
    if existing is not None:
        existing.usage_value = Decimal(str(metric["usage"])) if metric["usage"] is not None else None
        existing.limit_value = Decimal(str(metric["hard_limit"])) if metric["hard_limit"] is not None else None
        existing.last_measured_at = metric.get("last_measured_at")
        return

    message = (
        f"{metric['label']}: zuzycie {metric.get('usage')} / limit {metric.get('hard_limit')} "
        f"(status: {metric.get('status')})"
    )
    alert = ResourceLimitAlert(
        client=client,
        resource_key=metric["resource_key"],
        threshold_label=threshold_label,
        threshold_percent=threshold_percent,
        usage_value=Decimal(str(metric["usage"])) if metric["usage"] is not None else None,
        limit_value=Decimal(str(metric["hard_limit"])) if metric["hard_limit"] is not None else None,
        message=message[:255],
        triggered_at=datetime.utcnow(),
        last_measured_at=metric.get("last_measured_at"),
    )

    log_activity(
        "resources.alert_triggered",
        "resource_limit_alert",
        f"Alert limitu zasobow ({metric['resource_key']}:{threshold_label})",
        entity_id=metric["resource_key"],
        client=client,
        metadata={
            "resource_key": metric["resource_key"],
            "threshold_label": threshold_label,
            "threshold_percent": threshold_percent,
            "usage": metric.get("usage"),
            "hard_limit": metric.get("hard_limit"),
            "status": metric.get("status"),
        },
    )
    _notify_resource_alert(client, alert)
    from panel.extensions import db

    db.session.add(alert)


def _resolve_alert(client: Client, metric: dict, threshold_label: str) -> None:
    existing = _active_alert(client.id, metric["resource_key"], threshold_label)
    if existing is None:
        return
    existing.status = "resolved"
    existing.resolved_at = datetime.utcnow()
    existing.last_measured_at = metric.get("last_measured_at")
    log_activity(
        "resources.alert_resolved",
        "resource_limit_alert",
        f"Alert limitu zasobow rozwiazany ({metric['resource_key']}:{threshold_label})",
        entity_id=existing.id,
        client=client,
    )


def evaluate_client_resource_alerts(client: Client) -> dict:
    report = resource_usage_report(client)
    for metric in report.values():
        percent = metric.get("percent")
        status = metric.get("status")

        reached_80 = percent is not None and percent >= 80
        reached_95 = percent is not None and percent >= 95
        hard_exceeded = status == "hard_exceeded"

        if reached_80:
            _trigger_alert(client, metric, "threshold_80", 80)
        else:
            _resolve_alert(client, metric, "threshold_80")

        if reached_95:
            _trigger_alert(client, metric, "threshold_95", 95)
        else:
            _resolve_alert(client, metric, "threshold_95")

        if hard_exceeded:
            _trigger_alert(client, metric, "hard_limit", None)
        else:
            _resolve_alert(client, metric, "hard_limit")

    return report


def evaluate_all_clients_resource_alerts() -> dict:
    from panel.extensions import db

    clients = Client.query.order_by(Client.id.asc()).all()
    processed = 0
    active_alerts = 0
    for client in clients:
        evaluate_client_resource_alerts(client)
        processed += 1
    db.session.flush()
    active_alerts = ResourceLimitAlert.query.filter_by(status="active").count()
    return {"clients": processed, "active_alerts": active_alerts}


def hard_limit_block_reason(client: Client, resource_key: str, *, upcoming_delta: float = 0) -> str | None:
    report = resource_usage_report(client)
    metric = report.get(resource_key)
    if metric is None:
        return None
    hard_limit = metric.get("hard_limit")
    usage = metric.get("usage")
    if hard_limit is None or usage is None:
        return None
    if (usage + float(upcoming_delta)) < hard_limit:
        return None
    return (
        f"Przekroczono twardy limit zasobu {metric['label']} "
        f"({usage} + {upcoming_delta} / {hard_limit})."
    )


def estimate_upload_size(file_storage) -> int:
    stream = getattr(file_storage, "stream", None)
    if stream is None:
        return 0
    try:
        current_pos = stream.tell()
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(current_pos)
        return int(size)
    except Exception:
        return 0


def estimate_client_live_disk_mb(client: Client) -> float:
    username = client.user.username if client.user is not None else f"client-{client.id}"
    home = Path(current_app.config.get("CLIENT_HOME_ROOT", "storage/clients")) / username
    total_bytes = 0
    for root, _dirs, files in os.walk(home):
        for filename in files:
            target = Path(root) / filename
            try:
                total_bytes += target.stat().st_size
            except OSError:
                continue
    return round(total_bytes / (1024 * 1024), 2)


def estimate_client_live_inode_count(client: Client) -> int:
    username = client.user.username if client.user is not None else f"client-{client.id}"
    home = Path(current_app.config.get("CLIENT_HOME_ROOT", "storage/clients")) / username
    inode_count = 0
    for _root, dirs, files in os.walk(home):
        inode_count += len(dirs) + len(files)
    return inode_count
