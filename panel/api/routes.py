from __future__ import annotations

from datetime import datetime
from functools import wraps

from flask import Blueprint, g, jsonify, request

from panel.extensions import csrf, db
from panel.models import BillingTransaction, Ticket, TicketMessage
from panel.services.api_tokens import authenticate_api_token, parse_bearer_token
from panel.services.ticket_notifications import notify_client_staff_reply, notify_staff_client_reply, notify_staff_new_ticket
from panel.services.ticket_sla import apply_ticket_sla_defaults, mark_staff_first_response
from panel.services.webhooks import dispatch_webhook_event


api_bp = Blueprint("api", __name__)
csrf.exempt(api_bp)


TICKET_PRIORITY_VALUES = {"low", "normal", "high", "urgent"}
TICKET_CATEGORY_VALUES = {"hosting", "billing", "domain", "mail", "other"}


def _json_error(message: str, status: int):
    return jsonify({"error": message}), status


def _user_from_token():
    raw_token = parse_bearer_token(request.headers.get("Authorization"))
    if not raw_token:
        return None
    return authenticate_api_token(raw_token)


def token_auth_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        user = _user_from_token()
        if user is None:
            return _json_error("unauthorized", 401)
        if user.status not in {"active", "overdue", "suspended_financial"}:
            return _json_error("account_inactive", 403)
        g.api_user = user
        return func(*args, **kwargs)

    return wrapper


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
@token_auth_required
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
@token_auth_required
def billing_summary():
    user = g.api_user
    client = user.client_profile
    if client is None:
        return _json_error("client_profile_required", 403)

    transactions = (
        BillingTransaction.query.filter_by(client_id=client.id)
        .order_by(BillingTransaction.created_at.desc())
        .limit(20)
        .all()
    )

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
        }
    )


@api_bp.route("/api/v1/tickets", methods=["GET"])
@token_auth_required
def tickets_list():
    user = g.api_user
    query = Ticket.query
    status_filter = (request.args.get("status") or "").strip().lower()
    if status_filter:
        query = query.filter(Ticket.status == status_filter)

    if user.client_profile is not None:
        query = query.filter(Ticket.client_id == user.client_profile.id)

    tickets = query.order_by(Ticket.updated_at.desc()).limit(100).all()
    return jsonify({"tickets": [_ticket_payload(ticket) for ticket in tickets]})


@api_bp.route("/api/v1/tickets", methods=["POST"])
@token_auth_required
def tickets_create():
    user = g.api_user
    client = user.client_profile
    if client is None:
        return _json_error("client_profile_required", 403)

    payload = request.get_json(silent=True) or {}
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

    return jsonify({"ticket": _ticket_payload(ticket), "message": _message_payload(message)}), 201


@api_bp.route("/api/v1/tickets/<int:ticket_id>/replies", methods=["POST"])
@token_auth_required
def tickets_reply(ticket_id: int):
    user = g.api_user
    ticket = Ticket.query.get(ticket_id)
    if ticket is None:
        return _json_error("ticket_not_found", 404)

    if user.client_profile is not None and ticket.client_id != user.client_profile.id:
        return _json_error("ticket_not_found", 404)

    payload = request.get_json(silent=True) or {}
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

    return jsonify({"ticket": _ticket_payload(ticket), "message": _message_payload(message)}), 201
