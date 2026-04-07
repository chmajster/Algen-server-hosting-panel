from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import datetime

from flask import current_app

from panel.extensions import db
from panel.models import Client, WebhookDelivery, WebhookEndpoint


WEBHOOK_EVENT_TYPES = [
    ("ticket.created", "Ticket utworzony"),
    ("ticket.client_reply", "Ticket odpowiedz klienta"),
    ("ticket.staff_reply", "Ticket odpowiedz staffu"),
    ("ticket.escalated", "Ticket eskalowany"),
    ("payment.completed", "Platnosc zaksiegowana"),
    ("billing.suspended", "Klient zawieszony finansowo"),
    ("billing.resumed", "Klient wznowiony po platnosci"),
    ("service.plan_changed", "Zmiana planu uslugi"),
]


def webhook_event_values() -> list[str]:
    return [item[0] for item in WEBHOOK_EVENT_TYPES]


def normalize_event_types(raw_values: list[str] | tuple[str, ...] | None) -> list[str]:
    allowed = set(webhook_event_values())
    values = [value.strip() for value in (raw_values or []) if (value or "").strip() in allowed]
    # Preserve order and remove duplicates.
    return list(dict.fromkeys(values))


def _signature(secret: str, timestamp: str, body: str) -> str:
    payload = f"{timestamp}.{body}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _deliver(endpoint: WebhookEndpoint, event_type: str, body: str, payload: dict) -> WebhookDelivery:
    attempted_at = datetime.utcnow()
    delivery = WebhookDelivery(
        endpoint=endpoint,
        event_type=event_type,
        payload_json=payload,
        attempted_at=attempted_at,
    )
    db.session.add(delivery)

    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": event_type,
        "X-Webhook-Timestamp": timestamp,
    }
    if endpoint.secret:
        headers["X-Webhook-Signature"] = _signature(endpoint.secret, timestamp, body)

    request = urllib.request.Request(
        endpoint.target_url,
        data=body.encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            code = response.getcode()
            response_body = (response.read() or b"")[:500].decode("utf-8", errors="ignore")
            delivery.status_code = int(code)
            delivery.success = 200 <= int(code) < 300
            delivery.response_excerpt = response_body
            if delivery.success:
                endpoint.last_success_at = attempted_at
                endpoint.last_error = None
            else:
                endpoint.last_error = f"HTTP {code}: {response_body[:300]}"
    except urllib.error.HTTPError as exc:
        body_excerpt = (exc.read() or b"")[:500].decode("utf-8", errors="ignore")
        delivery.status_code = int(exc.code)
        delivery.success = False
        delivery.response_excerpt = body_excerpt
        endpoint.last_error = f"HTTP {exc.code}: {body_excerpt[:300]}"
    except Exception as exc:
        delivery.status_code = None
        delivery.success = False
        delivery.response_excerpt = str(exc)[:500]
        endpoint.last_error = str(exc)[:300]

    return delivery


def dispatch_webhook_event(event_type: str, payload: dict, *, client: Client | None = None) -> int:
    if not bool(current_app.config.get("WEBHOOKS_ENABLED", True)):
        return 0

    payload = dict(payload or {})
    payload.setdefault("event", event_type)
    payload.setdefault("timestamp_utc", datetime.utcnow().isoformat())

    query = WebhookEndpoint.query.filter_by(is_active=True)
    if client is not None:
        query = query.filter((WebhookEndpoint.client_id.is_(None)) | (WebhookEndpoint.client_id == client.id))

    delivered = 0
    body = json.dumps(payload, ensure_ascii=False)
    for endpoint in query.order_by(WebhookEndpoint.id.asc()).all():
        event_types = normalize_event_types(endpoint.event_types_json)
        if event_types and event_type not in event_types:
            continue
        _deliver(endpoint, event_type, body, payload)
        delivered += 1

    if delivered:
        db.session.commit()
    return delivered
