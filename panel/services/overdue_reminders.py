from __future__ import annotations

from datetime import date, datetime, timedelta

from flask import current_app

from panel.extensions import db
from panel.models import BillingCycle, OverdueReminder, User
from panel.services.audit import log_activity
from panel.services.mailer import send_plain_email


def _parse_offsets(value: str | None) -> list[int]:
    offsets: set[int] = set()
    raw = str(value or "").split(",")
    for item in raw:
        item = item.strip()
        if not item:
            continue
        try:
            parsed = int(item)
        except ValueError:
            continue
        offsets.add(max(0, parsed))
    if not offsets:
        return [0, 3, 7]
    return sorted(offsets)


def _subject(days_overdue: int) -> str:
    template = str(
        current_app.config.get(
            "BILLING_OVERDUE_REMINDER_SUBJECT",
            "Przypomnienie o zaleglej platnosci ({days} dni)",
        )
    )
    try:
        return template.format(days=days_overdue)
    except Exception:
        return f"Przypomnienie o zaleglej platnosci ({days_overdue} dni)"


def _body(*, username: str, service_name: str, amount, due_date: date, days_overdue: int) -> str:
    return "\n".join(
        [
            f"Witaj {username},",
            "",
            f"Twoja usluga {service_name} ma zalegla platnosc.",
            f"Kwota: {amount} PLN",
            f"Termin platnosci: {due_date.isoformat()}",
            f"Liczba dni po terminie: {days_overdue}",
            "",
            "Zaloguj sie do panelu i oplac fakture, aby uniknac blokady uslugi.",
        ]
    )


def send_overdue_reminders(*, actor: User | None = None, as_of: date | None = None) -> dict[str, int]:
    current = as_of or date.today()
    offsets = _parse_offsets(current_app.config.get("BILLING_OVERDUE_REMINDER_OFFSETS", "0,3,7"))

    summary = {
        "evaluated": 0,
        "sent": 0,
        "failed": 0,
        "skipped": 0,
    }

    due_cycles = (
        BillingCycle.query.join(BillingCycle.client_service)
        .filter(BillingCycle.status == "overdue")
        .order_by(BillingCycle.due_date.asc())
        .all()
    )

    for cycle in due_cycles:
        service = cycle.client_service
        if service is None or service.client is None or service.client.user is None:
            continue
        if not service.client.user.email:
            continue
        if cycle.due_date is None:
            continue

        days_overdue = max(0, (current - cycle.due_date).days)
        summary["evaluated"] += 1

        for day_offset in offsets:
            if days_overdue < day_offset:
                continue

            already_sent = OverdueReminder.query.filter_by(
                billing_cycle_id=cycle.id,
                reminder_type="email",
                day_offset=day_offset,
            ).first()
            if already_sent is not None:
                summary["skipped"] += 1
                continue

            subject = _subject(days_overdue)
            body = _body(
                username=service.client.user.full_name or service.client.user.username,
                service_name=service.name,
                amount=cycle.amount,
                due_date=cycle.due_date,
                days_overdue=days_overdue,
            )
            error = send_plain_email(to_email=service.client.user.email, subject=subject, body=body)

            reminder = OverdueReminder(
                client=service.client,
                client_service=service,
                billing_cycle=cycle,
                reminder_type="email",
                day_offset=day_offset,
                status="sent" if error is None else "failed",
                recipient=service.client.user.email,
                subject=subject,
                message=(error or "Wyslano")[:500],
                sent_at=datetime.utcnow(),
                metadata_json={
                    "days_overdue": days_overdue,
                    "service_status": service.status,
                },
            )
            db.session.add(reminder)

            if error is None:
                summary["sent"] += 1
            else:
                summary["failed"] += 1

            log_activity(
                "billing.overdue_reminder",
                "overdue_reminder",
                f"Wyslano przypomnienie overdue dla uslugi {service.name}",
                entity_id=service.id,
                client=service.client,
                actor=actor,
                success=error is None,
                metadata={
                    "billing_cycle_id": cycle.id,
                    "days_overdue": days_overdue,
                    "day_offset": day_offset,
                    "error": error,
                },
            )

    return summary


def overdue_reminder_stats(*, days: int = 30) -> dict[str, int]:
    cutoff = datetime.utcnow() - timedelta(days=max(1, int(days)))
    sent = OverdueReminder.query.filter(
        OverdueReminder.sent_at >= cutoff,
        OverdueReminder.status == "sent",
    ).count()
    failed = OverdueReminder.query.filter(
        OverdueReminder.sent_at >= cutoff,
        OverdueReminder.status == "failed",
    ).count()
    return {
        "sent": sent,
        "failed": failed,
    }
