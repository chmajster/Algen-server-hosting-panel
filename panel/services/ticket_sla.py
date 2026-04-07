from __future__ import annotations

from datetime import datetime, timedelta

from flask import current_app

from panel.extensions import db
from panel.models import Ticket
from panel.services.audit import log_activity


def first_response_sla_minutes() -> int:
    return max(5, int(current_app.config.get("TICKETS_SLA_FIRST_RESPONSE_MINUTES", 120)))


def escalation_minutes() -> int:
    return max(10, int(current_app.config.get("TICKETS_ESCALATION_MINUTES", 240)))


def apply_ticket_sla_defaults(ticket: Ticket, *, now: datetime | None = None) -> None:
    current = now or datetime.utcnow()
    if ticket.first_response_due_at is None:
        ticket.first_response_due_at = current + timedelta(minutes=first_response_sla_minutes())


def mark_staff_first_response(ticket: Ticket, *, now: datetime | None = None) -> None:
    current = now or datetime.utcnow()
    if ticket.first_response_at is None:
        ticket.first_response_at = current


def escalate_due_tickets() -> int:
    if not bool(current_app.config.get("TICKETS_ESCALATION_ENABLED", True)):
        return 0

    now = datetime.utcnow()
    effective_cutoff = now - timedelta(minutes=escalation_minutes())
    query = Ticket.query.filter(
        Ticket.status.in_(["open", "pending"]),
        Ticket.first_response_at.is_(None),
        Ticket.first_response_due_at.isnot(None),
        Ticket.first_response_due_at <= effective_cutoff,
        Ticket.escalated_at.is_(None),
    )

    processed = 0
    for ticket in query.all():
        ticket.escalated_at = now
        ticket.priority = "urgent"
        ticket.status = "pending"
        log_activity(
            "tickets.escalated",
            "ticket",
            f"Eskalowano ticket {ticket.display_number} po przekroczeniu SLA odpowiedzi.",
            entity_id=ticket.id,
            client=ticket.client,
            metadata={
                "first_response_due_at": ticket.first_response_due_at.isoformat() if ticket.first_response_due_at else None,
                "first_response_at": ticket.first_response_at.isoformat() if ticket.first_response_at else None,
            },
        )
        processed += 1

        # Lazy imports to avoid service circular dependencies.
        from panel.services.ticket_notifications import notify_staff_client_reply
        from panel.services.webhooks import dispatch_webhook_event

        dispatch_webhook_event(
            "ticket.escalated",
            {
                "ticket_id": ticket.id,
                "ticket": ticket.display_number,
                "subject": ticket.subject,
                "status": ticket.status,
                "priority": ticket.priority,
                "client": ticket.client.user.username if ticket.client and ticket.client.user else None,
            },
            client=ticket.client,
        )
        # Reuse staff notification channel with synthetic preview message.
        notify_staff_client_reply(
            ticket,
            type("EscalationMessage", (), {"message": "Ticket przekroczyl SLA i zostal eskalowany.", "author_user_id": None})(),
        )

    if processed:
        db.session.commit()
    return processed
