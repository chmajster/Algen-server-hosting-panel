from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from panel.extensions import db
from panel.models import (
    AccountSuspension,
    BillingCycle,
    BillingTransaction,
    Client,
    ClientBalance,
    ClientService,
    User,
)
from panel.services.audit import log_activity
from panel.utils.helpers import money


def ensure_client_balance(client: Client) -> ClientBalance:
    if client.balance is None:
        client.balance = ClientBalance(balance=Decimal("0.00"))
    return client.balance


def update_client_financial_status(client: Client, *, actor: User | None = None) -> None:
    balance = ensure_client_balance(client)
    if balance.balance >= 0:
        client.billing_status = "current"
        if client.user.status == "suspended_financial":
            client.user.status = "active"
        for service in client.services:
            if service.status == "pending_payment" and service.auto_resume and client.auto_resume_services:
                service.status = "active"
        for suspension in AccountSuspension.query.filter_by(client_id=client.id, active=True).all():
            if suspension.suspension_type == "financial":
                suspension.active = False
                suspension.released_at = datetime.utcnow()
    else:
        client.billing_status = "overdue"
        if client.user.status == "active":
            client.user.status = "suspended_financial"
        for service in client.services:
            if service.auto_suspend and service.status == "active":
                service.status = "pending_payment"
                db.session.add(
                    AccountSuspension(
                        client=client,
                        client_service=service,
                        actor=actor,
                        suspension_type="financial",
                        reason="Brak środków na saldzie",
                    )
                )


def adjust_balance(
    client: Client,
    amount: Decimal | str | float,
    transaction_type: str,
    description: str,
    *,
    actor: User | None = None,
    metadata: dict | None = None,
) -> BillingTransaction:
    balance = ensure_client_balance(client)
    delta = money(amount)
    balance.balance = money(balance.balance + delta)
    balance.last_recalculated_at = datetime.utcnow()
    transaction = BillingTransaction(
        client=client,
        actor=actor,
        amount=delta,
        transaction_type=transaction_type,
        description=description,
        balance_after=balance.balance,
        metadata_json=metadata or {},
    )
    db.session.add(transaction)
    log_activity(
        "billing.adjust_balance",
        "client_balance",
        f"Zmiana salda klienta {client.user.username}: {delta}",
        entity_id=client.id,
        client=client,
        actor=actor,
        metadata={"transaction_type": transaction_type, "amount": str(delta)},
    )
    update_client_financial_status(client, actor=actor)
    return transaction


def schedule_initial_cycle(service: ClientService) -> BillingCycle:
    cycle = BillingCycle(
        client_service=service,
        cycle_type=service.billing_period,
        amount=money(service.recurring_amount),
        due_date=date.today(),
        status="scheduled",
    )
    db.session.add(cycle)
    return cycle


def advance_due_date(cycle_type: str, current_due_date: date) -> date:
    if cycle_type == "daily":
        return current_due_date + timedelta(days=1)
    if cycle_type == "yearly":
        return current_due_date + timedelta(days=365)
    return current_due_date + timedelta(days=30)


def run_billing_cycle(*, actor: User | None = None) -> int:
    processed = 0
    due_cycles = BillingCycle.query.filter(
        BillingCycle.due_date <= date.today(),
        BillingCycle.status.in_(["scheduled", "overdue"]),
    ).all()
    for cycle in due_cycles:
        service = cycle.client_service
        client = service.client
        adjust_balance(
            client,
            -money(service.recurring_amount),
            "service_charge",
            f"Automatyczne naliczenie za usługę {service.name}",
            actor=actor,
            metadata={"service_id": service.id, "cycle_id": cycle.id},
        )
        cycle.last_charged_at = datetime.utcnow()
        cycle.status = "charged" if client.balance.balance >= 0 else "overdue"
        db.session.add(
            BillingCycle(
                client_service=service,
                cycle_type=cycle.cycle_type,
                amount=cycle.amount,
                due_date=advance_due_date(cycle.cycle_type, cycle.due_date),
                status="scheduled",
            )
        )
        processed += 1
    return processed
