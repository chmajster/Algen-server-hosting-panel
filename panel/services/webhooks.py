from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

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


def _build_idempotency_key(event_type: str, payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(f"{event_type}:{canonical}".encode("utf-8")).hexdigest()


def _retry_policy() -> tuple[int, int, int]:
    max_attempts = max(1, int(current_app.config.get("WEBHOOK_MAX_RETRIES", 5)))
    base_seconds = max(1, int(current_app.config.get("WEBHOOK_RETRY_BASE_SECONDS", 30)))
    cap_seconds = max(base_seconds, int(current_app.config.get("WEBHOOK_RETRY_MAX_SECONDS", 3600)))
    return max_attempts, base_seconds, cap_seconds


def _schedule_retry(delivery: WebhookDelivery) -> None:
    _max_attempts, base_seconds, cap_seconds = _retry_policy()
    backoff = min(cap_seconds, base_seconds * (2 ** max(0, delivery.attempt_count - 1)))
    delivery.next_retry_at = datetime.utcnow() + timedelta(seconds=backoff)


def _existing_delivery(endpoint: WebhookEndpoint, idempotency_key: str) -> WebhookDelivery | None:
    return (
        WebhookDelivery.query.filter_by(endpoint_id=endpoint.id, idempotency_key=idempotency_key)
        .order_by(WebhookDelivery.id.desc())
        .first()
    )


def _queue_delivery(
    endpoint: WebhookEndpoint,
    event_type: str,
    payload: dict,
    body: str,
    *,
    idempotency_key: str,
) -> WebhookDelivery:
    max_attempts, _base_seconds, _cap_seconds = _retry_policy()
    delivery = WebhookDelivery(
        endpoint=endpoint,
        event_type=event_type,
        payload_json=payload,
        idempotency_key=idempotency_key,
        destination_url=endpoint.target_url,
        max_attempts=max_attempts,
        request_body_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )
    db.session.add(delivery)
    return delivery


def _deliver(endpoint: WebhookEndpoint, event_type: str, body: str, payload: dict, *, idempotency_key: str) -> WebhookDelivery:
    attempted_at = datetime.utcnow()
    delivery = _existing_delivery(endpoint, idempotency_key)
    if delivery is None:
        delivery = _queue_delivery(endpoint, event_type, payload, body, idempotency_key=idempotency_key)

    if delivery.success:
        return delivery
    if delivery.dead_lettered:
        return delivery
    if delivery.next_retry_at is not None and delivery.next_retry_at > datetime.utcnow():
        return delivery

    delivery.attempt_count = int(delivery.attempt_count or 0) + 1
    delivery.attempted_at = attempted_at
    delivery.request_headers_json = {
        "Content-Type": "application/json",
        "X-Webhook-Event": event_type,
    }

    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json",
        "X-Webhook-Event": event_type,
        "X-Webhook-Timestamp": timestamp,
    }
    if endpoint.secret:
        headers["X-Webhook-Signature"] = _signature(endpoint.secret, timestamp, body)

    delivery.request_headers_json = {k: v for k, v in headers.items() if k != "X-Webhook-Signature"}

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
            delivery.next_retry_at = None
            if delivery.success:
                endpoint.last_success_at = attempted_at
                endpoint.last_error = None
                delivery.dead_lettered = False
                delivery.dead_letter_reason = None
                delivery.dead_lettered_at = None
            else:
                endpoint.last_error = f"HTTP {code}: {response_body[:300]}"
                if delivery.attempt_count >= delivery.max_attempts:
                    delivery.dead_lettered = True
                    delivery.dead_lettered_at = attempted_at
                    delivery.dead_letter_reason = endpoint.last_error
                    delivery.next_retry_at = None
                else:
                    _schedule_retry(delivery)
    except urllib.error.HTTPError as exc:
        body_excerpt = (exc.read() or b"")[:500].decode("utf-8", errors="ignore")
        delivery.status_code = int(exc.code)
        delivery.success = False
        delivery.response_excerpt = body_excerpt
        endpoint.last_error = f"HTTP {exc.code}: {body_excerpt[:300]}"
        if delivery.attempt_count >= delivery.max_attempts:
            delivery.dead_lettered = True
            delivery.dead_lettered_at = attempted_at
            delivery.dead_letter_reason = endpoint.last_error
            delivery.next_retry_at = None
        else:
            _schedule_retry(delivery)
    except Exception as exc:
        delivery.status_code = None
        delivery.success = False
        delivery.response_excerpt = str(exc)[:500]
        endpoint.last_error = str(exc)[:300]
        if delivery.attempt_count >= delivery.max_attempts:
            delivery.dead_lettered = True
            delivery.dead_lettered_at = attempted_at
            delivery.dead_letter_reason = endpoint.last_error
            delivery.next_retry_at = None
        else:
            _schedule_retry(delivery)

    try:
        from panel.services.event_stream import emit_event

        emit_event(
            event_type="webhook.delivery",
            message=(
                f"Webhook {event_type} -> endpoint #{endpoint.id}: "
                f"{'success' if delivery.success else 'failure'}"
            ),
            category="webhooks",
            severity="info" if delivery.success else ("error" if delivery.dead_lettered else "warning"),
            source="webhook",
            client=endpoint.client,
            payload={
                "delivery_id": delivery.id,
                "endpoint_id": endpoint.id,
                "event_type": event_type,
                "success": delivery.success,
                "status_code": delivery.status_code,
                "attempt_count": delivery.attempt_count,
                "dead_lettered": delivery.dead_lettered,
            },
            fingerprint=delivery.idempotency_key,
        )
    except Exception:
        pass

    return delivery


def replay_webhook_delivery(delivery: WebhookDelivery) -> WebhookDelivery:
    payload = dict(delivery.payload_json or {})
    payload.setdefault("event", delivery.event_type)
    payload.setdefault("timestamp_utc", datetime.utcnow().isoformat())
    body = json.dumps(payload, ensure_ascii=False)

    delivery.dead_lettered = False
    delivery.dead_lettered_at = None
    delivery.dead_letter_reason = None
    delivery.next_retry_at = None
    return _deliver(
        delivery.endpoint,
        delivery.event_type,
        body,
        payload,
        idempotency_key=delivery.idempotency_key or f"delivery:{delivery.id}",
    )


def process_webhook_retries(*, limit: int = 100) -> dict:
    now = datetime.utcnow()
    pending = (
        WebhookDelivery.query.filter(
            WebhookDelivery.success.is_(False),
            WebhookDelivery.dead_lettered.is_(False),
            WebhookDelivery.next_retry_at.isnot(None),
            WebhookDelivery.next_retry_at <= now,
        )
        .order_by(WebhookDelivery.next_retry_at.asc(), WebhookDelivery.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    processed = 0
    moved_to_dead_letter = 0
    delivered = 0

    for delivery in pending:
        payload = dict(delivery.payload_json or {})
        payload.setdefault("event", delivery.event_type)
        payload.setdefault("timestamp_utc", datetime.utcnow().isoformat())
        body = json.dumps(payload, ensure_ascii=False)
        _deliver(
            delivery.endpoint,
            delivery.event_type,
            body,
            payload,
            idempotency_key=delivery.idempotency_key or f"delivery:{delivery.id}",
        )
        processed += 1
        if delivery.success:
            delivered += 1
        elif delivery.dead_lettered:
            moved_to_dead_letter += 1

    return {
        "processed": processed,
        "delivered": delivered,
        "dead_lettered": moved_to_dead_letter,
    }


def deliver_to_endpoint(
    endpoint: WebhookEndpoint,
    event_type: str,
    payload: dict,
    *,
    idempotency_key: str | None = None,
    auto_commit: bool = True,
) -> WebhookDelivery:
    payload = dict(payload or {})
    payload.setdefault("event", event_type)
    payload.setdefault("timestamp_utc", datetime.utcnow().isoformat())
    body = json.dumps(payload, ensure_ascii=False)
    delivery = _deliver(
        endpoint,
        event_type,
        body,
        payload,
        idempotency_key=idempotency_key or _build_idempotency_key(event_type, payload),
    )
    if auto_commit:
        db.session.commit()
    return delivery


def dispatch_webhook_event(
    event_type: str,
    payload: dict,
    *,
    client: Client | None = None,
    idempotency_key: str | None = None,
    auto_commit: bool = True,
) -> int:
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
        endpoint_key = idempotency_key or _build_idempotency_key(event_type, {"endpoint_id": endpoint.id, **payload})
        _deliver(endpoint, event_type, body, payload, idempotency_key=endpoint_key)
        delivered += 1

    if delivered and auto_commit:
        db.session.commit()
    return delivered
