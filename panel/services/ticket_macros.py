from __future__ import annotations

import re

from panel.models import BillingCycle, ClientService, Domain, Ticket, TicketMacro, User


PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")

TICKET_MACRO_CATEGORIES = [
    ("billing", "Billing"),
    ("abuse", "Abuse"),
    ("migration", "Migration"),
    ("technical_support", "Technical support"),
    ("cancellation", "Cancellation"),
]

TICKET_MACRO_VISIBILITY = [
    ("all_staff", "Caly staff"),
    ("admin_only", "Tylko administratorzy"),
]


def macro_category_choices() -> list[tuple[str, str]]:
    return list(TICKET_MACRO_CATEGORIES)


def macro_visibility_choices() -> list[tuple[str, str]]:
    return list(TICKET_MACRO_VISIBILITY)


def visible_ticket_macros_for_user(user: User) -> list[TicketMacro]:
    query = TicketMacro.query.filter_by(is_active=True)
    if user.has_role("operator"):
        query = query.filter(TicketMacro.visibility_scope == "all_staff")
    return query.order_by(TicketMacro.sort_order.asc(), TicketMacro.name.asc()).all()


def _latest_service_for_ticket(ticket: Ticket):
    services = [service for service in ticket.client.services if service.status != "deleted"]
    if not services:
        return None
    services.sort(key=lambda item: item.created_at, reverse=True)
    return services[0]


def _latest_invoice_cycle_for_ticket(ticket: Ticket):
    return (
        BillingCycle.query.join(ClientService)
        .filter(ClientService.client_id == ticket.client_id)
        .order_by(BillingCycle.due_date.desc(), BillingCycle.id.desc())
        .first()
    )


def _preferred_domain_for_ticket(ticket: Ticket):
    return (
        Domain.query.filter_by(client_id=ticket.client_id)
        .order_by(Domain.is_primary.desc(), Domain.created_at.asc())
        .first()
    )


def build_ticket_macro_context(ticket: Ticket, *, actor: User | None = None) -> dict[str, str]:
    client_user = ticket.client.user if ticket.client is not None else None
    service = _latest_service_for_ticket(ticket)
    cycle = _latest_invoice_cycle_for_ticket(ticket)
    domain = _preferred_domain_for_ticket(ticket)
    assigned = ticket.assigned_to or actor

    invoice_number = f"INV-{cycle.id:06d}" if cycle is not None and cycle.id is not None else "-"
    due_date = cycle.due_date.strftime("%Y-%m-%d") if cycle is not None and cycle.due_date else "-"

    return {
        "client_full_name": client_user.full_name if client_user is not None else "-",
        "company_name": ticket.client.company_name or "-",
        "client_id": str(ticket.client_id or "-"),
        "service_name": service.name if service is not None else "-",
        "plan_name": service.plan.name if service is not None and service.plan is not None else "-",
        "invoice_number": invoice_number,
        "due_date": due_date,
        "ticket_id": ticket.display_number,
        "assigned_operator": assigned.full_name if assigned is not None else "-",
        "domain": domain.name if domain is not None else "-",
    }


def render_macro_template(template: str, context: dict[str, str]) -> tuple[str, str | None]:
    try:
        def _replace(match: re.Match[str]) -> str:
            key = (match.group(1) or "").strip()
            if not key:
                return ""
            value = context.get(key)
            if value is None:
                return match.group(0)
            return str(value)

        return PLACEHOLDER_PATTERN.sub(_replace, template or ""), None
    except Exception as exc:  # Defensive: never break ticket UI for malformed macro text.
        return template or "", str(exc)


def render_ticket_macro(*, macro: TicketMacro, ticket: Ticket, actor: User | None = None) -> tuple[str, str | None]:
    context = build_ticket_macro_context(ticket, actor=actor)
    return render_macro_template(macro.body_template or "", context)
