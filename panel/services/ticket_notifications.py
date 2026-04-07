from __future__ import annotations

from flask import current_app, has_request_context, url_for

from panel.models import Role, Ticket, TicketMessage, User
from panel.services.mailer import send_plain_email


STAFF_ROLE_NAMES = ("administrator", "operator")


def _notifications_enabled() -> bool:
    return bool(current_app.config.get("TICKETS_EMAIL_NOTIFICATIONS_ENABLED", True))


def _safe_subject(template: str, ticket: Ticket) -> str:
    base = template or "Ticket {ticket}"
    try:
        return base.format(ticket=ticket.display_number, subject=ticket.subject)
    except Exception:
        return f"Ticket {ticket.display_number}"


def _safe_external_url(endpoint: str, **kwargs) -> str:
    if has_request_context():
        return url_for(endpoint, _external=True, **kwargs)
    fallback = kwargs.get("ticket_id")
    if endpoint == "tickets.client_ticket_view":
        return f"/client/tickets/{fallback}"
    if endpoint == "tickets.admin_ticket_view":
        return f"/admin/tickets/{fallback}"
    return "/"


def _preview(text: str | None, max_len: int = 280) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= max_len:
        return collapsed
    return f"{collapsed[:max_len - 1]}..."


def _send_email(to_email: str, subject: str, body: str) -> bool:
    error = send_plain_email(to_email=to_email, subject=subject, body=body)
    if error:
        current_app.logger.warning("Ticket notification email failed to %s: %s", to_email, error)
        return False
    return True


def _staff_recipients(*, exclude_user_id: int | None = None) -> list[User]:
    users = (
        User.query.join(Role)
        .filter(Role.name.in_(STAFF_ROLE_NAMES))
        .order_by(User.username.asc())
        .all()
    )
    seen: set[str] = set()
    recipients: list[User] = []
    for user in users:
        if exclude_user_id is not None and user.id == exclude_user_id:
            continue
        email = (user.email or "").strip().lower()
        if not email or email in seen:
            continue
        seen.add(email)
        recipients.append(user)
    return recipients


def notify_staff_new_ticket(ticket: Ticket) -> int:
    if not _notifications_enabled():
        return 0

    subject = _safe_subject(
        str(current_app.config.get("TICKETS_EMAIL_SUBJECT_NEW_CLIENT_TICKET", "Nowy ticket klienta: {ticket}")),
        ticket,
    )
    staff_link = _safe_external_url("tickets.admin_ticket_view", ticket_id=ticket.id)
    client_link = _safe_external_url("tickets.client_ticket_view", ticket_id=ticket.id)
    client_login = ticket.client.user.username if ticket.client and ticket.client.user else "klient"
    body = (
        f"Nowe zgloszenie od klienta: {client_login}\n"
        f"Numer: {ticket.display_number}\n"
        f"Temat: {ticket.subject}\n"
        f"Kategoria: {ticket.category or '-'}\n"
        f"Priorytet: {ticket.priority}\n\n"
        f"Panel staff: {staff_link}\n"
        f"Podglad klienta: {client_link}\n"
    )

    sent = 0
    for user in _staff_recipients(exclude_user_id=ticket.created_by_user_id):
        if _send_email(user.email, subject, body):
            sent += 1
    return sent


def notify_staff_client_reply(ticket: Ticket, message: TicketMessage) -> int:
    if not _notifications_enabled():
        return 0

    subject = _safe_subject(
        str(current_app.config.get("TICKETS_EMAIL_SUBJECT_CLIENT_REPLY", "Nowa odpowiedz klienta: {ticket}")),
        ticket,
    )
    staff_link = _safe_external_url("tickets.admin_ticket_view", ticket_id=ticket.id)
    client_login = ticket.client.user.username if ticket.client and ticket.client.user else "klient"
    body = (
        f"Klient dodal odpowiedz do ticketu {ticket.display_number}.\n"
        f"Klient: {client_login}\n"
        f"Temat: {ticket.subject}\n"
        f"Podglad wiadomosci: {_preview(message.message)}\n\n"
        f"Przejdz do ticketu: {staff_link}\n"
    )

    sent = 0
    for user in _staff_recipients(exclude_user_id=message.author_user_id):
        if _send_email(user.email, subject, body):
            sent += 1
    return sent


def notify_client_staff_reply(ticket: Ticket, message: TicketMessage) -> bool:
    if not _notifications_enabled():
        return False

    client_user = ticket.client.user if ticket.client else None
    if client_user is None or not (client_user.email or "").strip():
        return False

    subject = _safe_subject(
        str(current_app.config.get("TICKETS_EMAIL_SUBJECT_STAFF_REPLY", "Nowa odpowiedz supportu: {ticket}")),
        ticket,
    )
    client_link = _safe_external_url("tickets.client_ticket_view", ticket_id=ticket.id)
    body = (
        f"Otrzymales nowa odpowiedz od zespolu supportu.\n"
        f"Numer: {ticket.display_number}\n"
        f"Temat: {ticket.subject}\n"
        f"Podglad wiadomosci: {_preview(message.message)}\n\n"
        f"Przejdz do ticketu: {client_link}\n"
    )
    return _send_email(client_user.email, subject, body)
