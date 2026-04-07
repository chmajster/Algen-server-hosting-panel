from __future__ import annotations

import hashlib
import json
from datetime import datetime
from functools import wraps

from flask import Blueprint, g, jsonify, request
from sqlalchemy import or_

from panel.extensions import csrf, db
from panel.models import ApiIdempotencyKey, BillingTransaction, EventStreamEntry, Ticket, TicketMessage
from panel.services.api_tokens import authenticate_api_token_record, parse_bearer_token
from panel.services.ticket_notifications import notify_client_staff_reply, notify_staff_client_reply, notify_staff_new_ticket
from panel.services.ticket_sla import apply_ticket_sla_defaults, mark_staff_first_response
from panel.services.webhooks import dispatch_webhook_event


api_bp = Blueprint("api", __name__)
csrf.exempt(api_bp)


TICKET_PRIORITY_VALUES = {"low", "normal", "high", "urgent"}
TICKET_CATEGORY_VALUES = {"hosting", "billing", "domain", "mail", "other"}
TICKET_STATUS_VALUES = {"open", "answered", "pending", "closed"}


def _json_error(code: str, status: int, detail: str | None = None):
    payload = {"error": code}
    if detail:
        payload["detail"] = detail
    return jsonify(payload), status


def _user_from_token():
    raw_token = parse_bearer_token(request.headers.get("Authorization"))
    if not raw_token:
        return None
    return authenticate_api_token_record(raw_token)


def _token_scopes() -> set[str]:
    token = getattr(g, "api_token", None)
    if token is None:
        return set()
    scopes = set(token.scopes_json or [])
    # Backward compatibility: historical tokens without scopes keep full access.
    if not scopes:
        return {
            "profile:read",
            "billing:read",
            "tickets:read",
            "tickets:write",
            "backups:read",
            "monitoring:read",
            "status:read",
            "events:read",
        }
    return scopes


def token_auth_required(*required_scopes: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            token = _user_from_token()
            if token is None:
                return _json_error("unauthorized", 401)
            user = token.user
            if user.status not in {"active", "overdue", "suspended_financial"}:
                return _json_error("account_inactive", 403)
            if required_scopes:
                available = set(token.scopes_json or [])
                if not available:
                    available = {
                        "profile:read",
                        "billing:read",
                        "tickets:read",
                        "tickets:write",
                        "backups:read",
                        "monitoring:read",
                        "status:read",
                        "events:read",
                    }
                if not set(required_scopes).issubset(available):
                    return _json_error("invalid_scope", 403)
            g.api_token = token
            g.api_user = user
            return func(*args, **kwargs)

        return wrapper

    return decorator


def _parse_pagination(*, default_per_page: int = 20, max_per_page: int = 100) -> tuple[int, int, tuple | None]:
    page_raw = (request.args.get("page") or "1").strip()
    per_page_raw = (request.args.get("per_page") or str(default_per_page)).strip()
    try:
        page = int(page_raw)
        per_page = int(per_page_raw)
    except ValueError:
        return 1, default_per_page, _json_error("bad_pagination", 400, "page/per_page musza byc liczbami calkowitymi.")
    if page < 1 or per_page < 1 or per_page > max_per_page:
        return 1, default_per_page, _json_error("bad_pagination", 400, "Nieprawidlowy zakres page/per_page.")
    return page, per_page, None


def _pagination_meta(*, page: int, per_page: int, total: int) -> dict:
    pages = (total + per_page - 1) // per_page if per_page else 1
    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": pages,
    }


def _request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    content = f"{request.method}:{request.path}:{canonical}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _idempotency_precheck(payload: dict) -> tuple[str | None, str | None, tuple | None]:
    key = (request.headers.get("Idempotency-Key") or "").strip()
    if not key:
        # Keep backward compatibility with older API clients that do not send idempotency headers.
        return None, None, None

    token = g.api_token
    req_hash = _request_hash(payload)
    existing = ApiIdempotencyKey.query.filter_by(
        api_token_id=token.id,
        idempotency_key=key,
        method=request.method,
        path=request.path,
    ).first()
    if existing is None:
        return key, req_hash, None
    if existing.request_hash != req_hash:
        return None, None, _json_error("duplicate_idempotent_request", 409)
    return key, req_hash, (jsonify(existing.response_body_json or {}), existing.response_status)


def _idempotency_store(key: str | None, req_hash: str | None, status: int, payload: dict) -> None:
    if not key or not req_hash:
        return

    token = g.api_token
    record = ApiIdempotencyKey(
        api_token=token,
        idempotency_key=key,
        method=request.method,
        path=request.path,
        request_hash=req_hash,
        response_status=status,
        response_body_json=payload,
        processed_at=datetime.utcnow(),
    )
    db.session.add(record)


def _ticket_payload(ticket: Ticket) -> dict:
    return {
        "id": ticket.id,
        "number": ticket.display_number,
        "subject": ticket.subject,
        "category": ticket.category,
        "priority": ticket.priority,
        "status": ticket.status,
        "client_id": ticket.client_id,
        "created_by_user_id": ticket.created_by_user_id,
        "assigned_to_user_id": ticket.assigned_to_user_id,
        "first_response_due_at": ticket.first_response_due_at.isoformat() if ticket.first_response_due_at else None,
        "first_response_at": ticket.first_response_at.isoformat() if ticket.first_response_at else None,
        "escalated_at": ticket.escalated_at.isoformat() if ticket.escalated_at else None,
        "closed_at": ticket.closed_at.isoformat() if ticket.closed_at else None,
        "updated_at": ticket.updated_at.isoformat() if ticket.updated_at else None,
    }


def _message_payload(message: TicketMessage) -> dict:
    return {
        "id": message.id,
        "ticket_id": message.ticket_id,
        "author_user_id": message.author_user_id,
        "is_internal": message.is_internal,
        "message": message.message,
        "created_at": message.created_at.isoformat() if message.created_at else None,
    }


@api_bp.route("/api/v1/me", methods=["GET"])
@token_auth_required("profile:read")
def me():
    user = g.api_user
    client = user.client_profile
    return jsonify(
        {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "role": user.role.name if user.role else None,
            "status": user.status,
            "client": {
                "id": client.id,
                "billing_status": client.billing_status,
                "balance": str(client.balance.balance if client.balance else "0.00"),
                "currency": client.balance.currency if client.balance else "PLN",
            }
            if client
            else None,
        }
    )


@api_bp.route("/api/v1/billing/summary", methods=["GET"])
@token_auth_required("billing:read")
def billing_summary():
    user = g.api_user
    client = user.client_profile
    if client is None:
        return _json_error("client_profile_required", 403)

    page, per_page, error = _parse_pagination(default_per_page=20, max_per_page=100)
    if error is not None:
        return error

    tx_query = BillingTransaction.query.filter_by(client_id=client.id).order_by(BillingTransaction.created_at.desc())
    tx_type = (request.args.get("type") or "").strip().lower()
    if tx_type:
        allowed_types = {"topup", "deduction", "bonus", "correction", "refund", "manual_fee", "service_charge", "topup_online", "plan_change_proration", "topup_manual"}
        if tx_type not in allowed_types:
            return _json_error("bad_filter", 400, "Nieobslugiwany filtr typu transakcji.")
        tx_query = tx_query.filter(BillingTransaction.transaction_type == tx_type)

    total = tx_query.count()
    transactions = tx_query.offset((page - 1) * per_page).limit(per_page).all()

    return jsonify(
        {
            "client_id": client.id,
            "billing_status": client.billing_status,
            "balance": str(client.balance.balance if client.balance else "0.00"),
            "currency": client.balance.currency if client.balance else "PLN",
            "services": [
                {
                    "id": service.id,
                    "name": service.name,
                    "type": service.service_type,
                    "status": service.status,
                    "billing_period": service.billing_period,
                    "recurring_amount": str(service.recurring_amount),
                    "plan": service.plan.name if service.plan else None,
                }
                for service in client.services
            ],
            "transactions": [
                {
                    "id": tx.id,
                    "created_at": tx.created_at.isoformat() if tx.created_at else None,
                    "type": tx.transaction_type,
                    "amount": str(tx.amount),
                    "balance_after": str(tx.balance_after),
                    "description": tx.description,
                }
                for tx in transactions
            ],
            "meta": _pagination_meta(page=page, per_page=per_page, total=total),
        }
    )


@api_bp.route("/api/v1/events", methods=["GET"])
@token_auth_required("events:read")
def events_list():
    user = g.api_user

    page, per_page, error = _parse_pagination(default_per_page=30, max_per_page=200)
    if error is not None:
        return error

    category = (request.args.get("category") or "").strip().lower()
    severity = (request.args.get("severity") or "").strip().lower()
    event_type = (request.args.get("event_type") or "").strip()
    search = (request.args.get("search") or "").strip()

    query = EventStreamEntry.query

    if user.client_profile is not None:
        query = query.filter(
            or_(
                EventStreamEntry.client_id.is_(None),
                EventStreamEntry.client_id == user.client_profile.id,
            )
        )
    else:
        client_id_raw = (request.args.get("client_id") or "").strip()
        if client_id_raw:
            if not client_id_raw.isdigit():
                return _json_error("bad_filter", 400, "client_id musi byc liczba calkowita.")
            query = query.filter(EventStreamEntry.client_id == int(client_id_raw))

    if category:
        query = query.filter(EventStreamEntry.category == category)
    if severity:
        query = query.filter(EventStreamEntry.severity == severity)
    if event_type:
        query = query.filter(EventStreamEntry.event_type == event_type)
    if search:
        like_value = f"%{search}%"
        query = query.filter(
            or_(
                EventStreamEntry.message.ilike(like_value),
                EventStreamEntry.event_type.ilike(like_value),
                EventStreamEntry.source.ilike(like_value),
            )
        )

    total = query.count()
    rows = query.order_by(EventStreamEntry.event_at.desc(), EventStreamEntry.id.desc()).offset((page - 1) * per_page).limit(per_page).all()

    return jsonify(
        {
            "events": [
                {
                    "id": row.id,
                    "type": row.event_type,
                    "tenant": row.client_id,
                    "actor": row.actor_user_id,
                    "timestamp": row.event_at.isoformat() if row.event_at else None,
                    "reference_id": str((row.payload_json or {}).get("reference_id"))
                    if (row.payload_json or {}).get("reference_id") is not None
                    else None,
                    "event_type": row.event_type,
                    "category": row.category,
                    "severity": row.severity,
                    "source": row.source,
                    "message": row.message,
                    "client_id": row.client_id,
                    "actor_user_id": row.actor_user_id,
                    "event_at": row.event_at.isoformat() if row.event_at else None,
                    "payload": row.payload_json or {},
                }
                for row in rows
            ],
            "meta": _pagination_meta(page=page, per_page=per_page, total=total),
        }
    )


@api_bp.route("/api/v1/tickets", methods=["GET"])
@token_auth_required("tickets:read")
def tickets_list():
    user = g.api_user
    page, per_page, error = _parse_pagination(default_per_page=20, max_per_page=100)
    if error is not None:
        return error

    query = Ticket.query
    status_filter = (request.args.get("status") or "").strip().lower()
    if status_filter:
        if status_filter not in TICKET_STATUS_VALUES:
            return _json_error("bad_filter", 400, "Nieobslugiwany status ticketu.")
        query = query.filter(Ticket.status == status_filter)

    priority_filter = (request.args.get("priority") or "").strip().lower()
    if priority_filter:
        if priority_filter not in TICKET_PRIORITY_VALUES:
            return _json_error("bad_filter", 400, "Nieobslugiwany priorytet ticketu.")
        query = query.filter(Ticket.priority == priority_filter)

    category_filter = (request.args.get("category") or "").strip().lower()
    if category_filter:
        if category_filter not in TICKET_CATEGORY_VALUES:
            return _json_error("bad_filter", 400, "Nieobslugiwana kategoria ticketu.")
        query = query.filter(Ticket.category == category_filter)

    if user.client_profile is not None:
        query = query.filter(Ticket.client_id == user.client_profile.id)

    total = query.count()
    tickets = query.order_by(Ticket.updated_at.desc()).offset((page - 1) * per_page).limit(per_page).all()
    return jsonify(
        {
            "tickets": [_ticket_payload(ticket) for ticket in tickets],
            "meta": _pagination_meta(page=page, per_page=per_page, total=total),
        }
    )


@api_bp.route("/api/v1/tickets", methods=["POST"])
@token_auth_required("tickets:write")
def tickets_create():
    user = g.api_user
    client = user.client_profile
    if client is None:
        return _json_error("client_profile_required", 403)

    payload = request.get_json(silent=True) or {}
    idem_key, idem_hash, idem_cached = _idempotency_precheck(payload)
    if idem_cached is not None:
        return idem_cached

    subject = str(payload.get("subject") or "").strip()
    message_text = str(payload.get("message") or "").strip()
    category = str(payload.get("category") or "other").strip().lower()
    priority = str(payload.get("priority") or "normal").strip().lower()

    if len(subject) < 5 or len(subject) > 200:
        return _json_error("invalid_subject", 400)
    if len(message_text) < 2 or len(message_text) > 8000:
        return _json_error("invalid_message", 400)
    if category not in TICKET_CATEGORY_VALUES:
        return _json_error("invalid_category", 400)
    if priority not in TICKET_PRIORITY_VALUES:
        return _json_error("invalid_priority", 400)

    now = datetime.utcnow()
    ticket = Ticket(
        client=client,
        created_by=user,
        subject=subject,
        category=category,
        priority=priority,
        status="open",
        last_message_at=now,
        metadata_json={"source": "api"},
    )
    apply_ticket_sla_defaults(ticket, now=now)
    message = TicketMessage(ticket=ticket, author=user, message=message_text)
    db.session.add(ticket)
    db.session.add(message)
    db.session.commit()

    notify_staff_new_ticket(ticket)
    dispatch_webhook_event(
        "ticket.created",
        {
            "ticket_id": ticket.id,
            "ticket": ticket.display_number,
            "subject": ticket.subject,
            "status": ticket.status,
            "priority": ticket.priority,
            "category": ticket.category,
            "source": "api",
            "client": client.user.username if client.user else None,
        },
        client=client,
    )

    response_payload = {"ticket": _ticket_payload(ticket), "message": _message_payload(message)}
    _idempotency_store(idem_key, idem_hash, 201, response_payload)
    db.session.commit()
    return jsonify(response_payload), 201


@api_bp.route("/api/v1/tickets/<int:ticket_id>/replies", methods=["POST"])
@token_auth_required("tickets:write")
def tickets_reply(ticket_id: int):
    user = g.api_user
    ticket = Ticket.query.get(ticket_id)
    if ticket is None:
        return _json_error("ticket_not_found", 404)

    if user.client_profile is not None and ticket.client_id != user.client_profile.id:
        return _json_error("ticket_not_found", 404)

    payload = request.get_json(silent=True) or {}
    idem_key, idem_hash, idem_cached = _idempotency_precheck(payload)
    if idem_cached is not None:
        return idem_cached

    message_text = str(payload.get("message") or "").strip()
    if len(message_text) < 2 or len(message_text) > 8000:
        return _json_error("invalid_message", 400)

    message = TicketMessage(ticket=ticket, author=user, message=message_text)
    ticket.last_message_at = datetime.utcnow()
    ticket.closed_at = None

    if user.client_profile is not None:
        ticket.status = "open"
    else:
        ticket.status = "answered"
        mark_staff_first_response(ticket)

    db.session.add(message)
    db.session.commit()

    if user.client_profile is not None:
        notify_staff_client_reply(ticket, message)
        dispatch_webhook_event(
            "ticket.client_reply",
            {
                "ticket_id": ticket.id,
                "ticket": ticket.display_number,
                "message_id": message.id,
                "status": ticket.status,
                "priority": ticket.priority,
                "source": "api",
            },
            client=ticket.client,
        )
    else:
        notify_client_staff_reply(ticket, message)
        dispatch_webhook_event(
            "ticket.staff_reply",
            {
                "ticket_id": ticket.id,
                "ticket": ticket.display_number,
                "message_id": message.id,
                "status": ticket.status,
                "priority": ticket.priority,
                "source": "api",
                "staff": user.username,
            },
            client=ticket.client,
        )

    response_payload = {"ticket": _ticket_payload(ticket), "message": _message_payload(message)}
    _idempotency_store(idem_key, idem_hash, 201, response_payload)
    db.session.commit()
    return jsonify(response_payload), 201
