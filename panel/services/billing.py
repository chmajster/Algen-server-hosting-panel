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
    ServicePlan,
    User,
)
from panel.services.audit import log_activity
from panel.services.client_apache import ClientApacheServiceError, sync_client_apache_instance
from panel.utils.helpers import money


def ensure_client_balance(client: Client) -> ClientBalance:
    if client.balance is None:
        client.balance = ClientBalance(balance=Decimal("0.00"))
    return client.balance


def update_client_financial_status(client: Client, *, actor: User | None = None) -> None:
    previous_billing_status = client.billing_status
    previous_user_status = client.user.status if client.user is not None else None
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

    if previous_billing_status != client.billing_status or previous_user_status != (client.user.status if client.user else None):
        # Lazy import to avoid service circular dependencies.
        from panel.services.webhooks import dispatch_webhook_event

        if client.billing_status == "overdue":
            dispatch_webhook_event(
                "billing.suspended",
                {
                    "client_id": client.id,
                    "username": client.user.username if client.user else None,
                    "balance": str(balance.balance),
                    "billing_status": client.billing_status,
                },
                client=client,
                auto_commit=False,
            )
        elif previous_billing_status == "overdue" and client.billing_status == "current":
            dispatch_webhook_event(
                "billing.resumed",
                {
                    "client_id": client.id,
                    "username": client.user.username if client.user else None,
                    "balance": str(balance.balance),
                    "billing_status": client.billing_status,
                },
                client=client,
                auto_commit=False,
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


def billing_period_days(cycle_type: str) -> int:
    if cycle_type == "daily":
        return 1
    if cycle_type == "yearly":
        return 365
    return 30


def plan_price_for_period(plan: ServicePlan, billing_period: str) -> Decimal:
    if billing_period == "daily":
        return money(plan.daily_price or 0)
    if billing_period == "yearly":
        return money(plan.yearly_price or 0)
    return money(plan.monthly_price or 0)


def _cycle_remaining_days(service: ClientService, *, as_of: date) -> tuple[int, int, date]:
    cycle_days = billing_period_days(service.billing_period)
    next_cycle = (
        BillingCycle.query.filter_by(client_service_id=service.id)
        .filter(BillingCycle.status.in_(["scheduled", "overdue"]))
        .order_by(BillingCycle.due_date.asc())
        .first()
    )
    if next_cycle is not None and next_cycle.due_date is not None:
        cycle_end = next_cycle.due_date
    else:
        cycle_end = as_of + timedelta(days=cycle_days)

    cycle_start = cycle_end - timedelta(days=cycle_days)
    if as_of < cycle_start:
        remaining_days = cycle_days
    else:
        remaining_days = max(0, (cycle_end - as_of).days)
    return remaining_days, cycle_days, cycle_end


def change_service_plan_with_proration(
    service: ClientService,
    new_plan: ServicePlan,
    *,
    actor: User | None = None,
    as_of: date | None = None,
) -> dict:
    if service.service_plan_id == new_plan.id:
        return {
            "changed": False,
            "reason": "same_plan",
            "service_id": service.id,
            "old_plan_id": new_plan.id,
            "new_plan_id": new_plan.id,
            "balance_delta": Decimal("0.00"),
        }

    effective_date = as_of or date.today()
    old_plan = service.plan
    old_amount = money(service.recurring_amount)
    new_amount = plan_price_for_period(new_plan, service.billing_period)
    remaining_days, cycle_days, cycle_end = _cycle_remaining_days(service, as_of=effective_date)

    difference = new_amount - old_amount
    prorated_difference = money((difference * Decimal(remaining_days)) / Decimal(cycle_days)) if remaining_days > 0 else Decimal("0.00")
    balance_delta = -prorated_difference

    if balance_delta != Decimal("0.00"):
        adjust_balance(
            service.client,
            balance_delta,
            "plan_change_proration",
            f"Prorata po zmianie planu uslugi {service.name}",
            actor=actor,
            metadata={
                "service_id": service.id,
                "old_plan_id": old_plan.id if old_plan else None,
                "new_plan_id": new_plan.id,
                "remaining_days": remaining_days,
                "cycle_days": cycle_days,
            },
        )

    service.service_plan_id = new_plan.id
    service.recurring_amount = new_amount
    metadata = dict(service.metadata_json or {})
    metadata["plan_change"] = {
        "old_plan_id": old_plan.id if old_plan else None,
        "new_plan_id": new_plan.id,
        "changed_at": datetime.utcnow().isoformat(),
        "remaining_days": remaining_days,
        "cycle_days": cycle_days,
        "cycle_end": cycle_end.isoformat(),
        "proration": str(balance_delta),
    }
    service.metadata_json = metadata

    scheduled_cycles = BillingCycle.query.filter_by(client_service_id=service.id, status="scheduled").all()
    for cycle in scheduled_cycles:
        cycle.amount = new_amount

    apache_sync = None
    if service.service_type == "hosting":
        try:
            apache_sync = sync_client_apache_instance(service.client, reason="billing_plan_change", actor=actor)
        except ClientApacheServiceError as exc:
            apache_sync = {"error": str(exc)}

    log_activity(
        "billing.service_plan_change",
        "client_service",
        f"Zmieniono plan uslugi {service.name}",
        entity_id=service.id,
        client=service.client,
        actor=actor,
        metadata={
            "old_plan_id": old_plan.id if old_plan else None,
            "new_plan_id": new_plan.id,
            "old_amount": str(old_amount),
            "new_amount": str(new_amount),
            "balance_delta": str(balance_delta),
            "remaining_days": remaining_days,
            "cycle_days": cycle_days,
            "apache_sync": apache_sync,
        },
    )
    return {
        "changed": True,
        "service_id": service.id,
        "old_plan_id": old_plan.id if old_plan else None,
        "new_plan_id": new_plan.id,
        "old_amount": old_amount,
        "new_amount": new_amount,
        "balance_delta": balance_delta,
        "remaining_days": remaining_days,
        "cycle_days": cycle_days,
        "cycle_end": cycle_end,
    }


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
