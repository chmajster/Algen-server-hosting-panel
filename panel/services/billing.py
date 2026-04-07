from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import current_app

from panel.extensions import db
from panel.models import (
    AccountSuspension,
    BillingCycle,
    BillingTransaction,
    Client,
    ClientBalance,
    ClientService,
    PaymentSetting,
    ServicePlan,
    User,
    UserStatusHistory,
)
from panel.services.audit import log_activity
from panel.services.client_apache import (
    ClientApacheServiceError,
    resume_client_apache_instance,
    suspend_client_apache_instance,
    sync_client_apache_instance,
)
from panel.utils.helpers import money


FINANCIAL_ENFORCEMENT_LABELS = {
    "active": "active",
    "overdue": "overdue",
    "in_grace_period": "in grace period",
    "suspended_non_payment": "suspended for non-payment",
    "manually_suspended": "manually suspended",
}


def _to_non_negative_int(value, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(0, parsed)


def _global_payment_setting() -> PaymentSetting | None:
    return PaymentSetting.query.filter_by(client_id=None).first()


def _client_payment_setting(client: Client, *, global_setting: PaymentSetting | None = None) -> PaymentSetting | None:
    setting = PaymentSetting.query.filter_by(client_id=client.id).first()
    if setting is not None:
        return setting
    return global_setting


def resolve_service_grace_days(
    service: ClientService,
    *,
    client_setting: PaymentSetting | None = None,
    global_setting: PaymentSetting | None = None,
) -> int:
    if service.plan is not None and service.plan.grace_days_override is not None:
        return _to_non_negative_int(service.plan.grace_days_override, 0)
    if client_setting is not None:
        return _to_non_negative_int(client_setting.grace_days, 0)
    if global_setting is not None:
        return _to_non_negative_int(global_setting.grace_days, 0)
    return _to_non_negative_int(current_app.config.get("BILLING_GRACE_DAYS", 3), 3)


def _service_overdue_cycles(service: ClientService) -> list[BillingCycle]:
    return (
        BillingCycle.query.filter_by(client_service_id=service.id, status="overdue")
        .order_by(BillingCycle.due_date.asc())
        .all()
    )


def resolve_service_financial_enforcement_state(
    service: ClientService,
    *,
    as_of: date | None = None,
    client_setting: PaymentSetting | None = None,
    global_setting: PaymentSetting | None = None,
) -> dict:
    current = as_of or date.today()
    if service.status == "blocked_manual":
        return {
            "code": "manually_suspended",
            "label": FINANCIAL_ENFORCEMENT_LABELS["manually_suspended"],
            "overdue_since": None,
            "grace_days": resolve_service_grace_days(service, client_setting=client_setting, global_setting=global_setting),
            "grace_until": None,
            "manual_override": bool(service.financial_enforcement_override),
            "service_status": service.status,
        }

    overdue_cycles = _service_overdue_cycles(service)
    grace_days = resolve_service_grace_days(service, client_setting=client_setting, global_setting=global_setting)
    if not overdue_cycles:
        return {
            "code": "active",
            "label": FINANCIAL_ENFORCEMENT_LABELS["active"],
            "overdue_since": None,
            "grace_days": grace_days,
            "grace_until": None,
            "manual_override": bool(service.financial_enforcement_override),
            "service_status": service.status,
        }

    overdue_since = overdue_cycles[0].due_date
    grace_until = overdue_since + timedelta(days=grace_days)
    if grace_days <= 0 or current >= grace_until:
        code = "suspended_non_payment"
    elif current == overdue_since:
        code = "overdue"
    else:
        code = "in_grace_period"

    return {
        "code": code,
        "label": FINANCIAL_ENFORCEMENT_LABELS[code],
        "overdue_since": overdue_since,
        "grace_days": grace_days,
        "grace_until": grace_until,
        "overdue_cycles": len(overdue_cycles),
        "manual_override": bool(service.financial_enforcement_override),
        "service_status": service.status,
    }


def financial_enforcement_snapshot(services: list[ClientService], *, as_of: date | None = None) -> dict[int, dict]:
    current = as_of or date.today()
    global_setting = _global_payment_setting()
    client_settings: dict[int, PaymentSetting | None] = {}
    snapshot: dict[int, dict] = {}

    for service in services:
        if service.client_id not in client_settings:
            client_settings[service.client_id] = _client_payment_setting(service.client, global_setting=global_setting)
        snapshot[service.id] = resolve_service_financial_enforcement_state(
            service,
            as_of=current,
            client_setting=client_settings[service.client_id],
            global_setting=global_setting,
        )
    return snapshot


def _effective_auto_resume(
    service: ClientService,
    *,
    client_setting: PaymentSetting | None,
    global_setting: PaymentSetting | None,
) -> bool:
    if not bool(current_app.config.get("BILLING_AUTO_RESUME", True)):
        return False
    setting = client_setting or global_setting
    if setting is not None and not bool(setting.auto_resume):
        return False
    return bool(service.auto_resume and service.client.auto_resume_services)


def _ensure_financial_suspension(service: ClientService, *, actor: User | None, reason: str) -> None:
    existing = AccountSuspension.query.filter_by(
        client_id=service.client_id,
        client_service_id=service.id,
        suspension_type="financial",
        active=True,
    ).first()
    if existing is None:
        db.session.add(
            AccountSuspension(
                client=service.client,
                client_service=service,
                actor=actor,
                suspension_type="financial",
                reason=reason,
            )
        )


def _release_financial_suspensions(service: ClientService) -> int:
    released = 0
    now = datetime.utcnow()
    active_rows = AccountSuspension.query.filter_by(
        client_id=service.client_id,
        client_service_id=service.id,
        suspension_type="financial",
        active=True,
    ).all()
    for row in active_rows:
        row.active = False
        row.released_at = now
        released += 1
    return released


def _apply_hosting_financial_hook(service: ClientService, action: str, *, actor: User | None) -> dict | None:
    if service.service_type != "hosting":
        return None
    try:
        if action == "suspend":
            return suspend_client_apache_instance(service.client, reason="financial_suspension", actor=actor)
        if action == "resume":
            return resume_client_apache_instance(service.client, reason="financial_unsuspension", actor=actor)
    except ClientApacheServiceError as exc:
        return {"error": str(exc)}
    return None


def _settle_overdue_cycles_if_balance_covered(client: Client) -> int:
    balance = ensure_client_balance(client)
    if balance.balance < 0:
        return 0

    now = datetime.utcnow()
    overdue_cycles = (
        BillingCycle.query.join(ClientService, BillingCycle.client_service_id == ClientService.id)
        .filter(ClientService.client_id == client.id, BillingCycle.status == "overdue")
        .all()
    )
    for cycle in overdue_cycles:
        cycle.status = "charged"
        cycle.last_charged_at = cycle.last_charged_at or now
    return len(overdue_cycles)


def _derive_client_financial_status(client: Client, states: list[dict]) -> str:
    if any(state["code"] == "manually_suspended" for state in states):
        return "manually_suspended"
    if any(service.status == "suspended" for service in client.services if service.status != "deleted"):
        return "suspended_non_payment"
    if any(state["code"] == "in_grace_period" for state in states):
        return "in_grace_period"
    if any(state["code"] in {"overdue", "suspended_non_payment"} for state in states):
        return "overdue"
    return "current"


def _log_service_transition(
    service: ClientService,
    *,
    old_status: str,
    new_status: str,
    state: dict,
    actor: User | None,
    provisioning_result: dict | None,
) -> None:
    log_activity(
        "billing.financial_enforcement_transition",
        "client_service",
        f"Zmiana statusu egzekucji finansowej uslugi {service.name}: {old_status} -> {new_status}",
        entity_id=service.id,
        client=service.client,
        actor=actor,
        metadata={
            "old_status": old_status,
            "new_status": new_status,
            "financial_state": state["code"],
            "grace_days": state.get("grace_days"),
            "overdue_since": state.get("overdue_since").isoformat() if state.get("overdue_since") else None,
            "manual_override": bool(service.financial_enforcement_override),
            "provisioning": provisioning_result,
        },
    )


def ensure_client_balance(client: Client) -> ClientBalance:
    if client.balance is None:
        client.balance = ClientBalance(balance=Decimal("0.00"))
    return client.balance


def update_client_financial_status(client: Client, *, actor: User | None = None) -> None:
    update_client_financial_status_for_date(client, actor=actor, as_of=date.today())


def update_client_financial_status_for_date(
    client: Client,
    *,
    actor: User | None = None,
    as_of: date | None = None,
) -> dict:
    current = as_of or date.today()
    previous_billing_status = client.billing_status
    previous_user_status = client.user.status if client.user is not None else None

    settled_cycles = _settle_overdue_cycles_if_balance_covered(client)
    global_setting = _global_payment_setting()
    client_setting = _client_payment_setting(client, global_setting=global_setting)

    transitions = 0
    states: list[dict] = []

    for service in client.services:
        if service.status == "deleted":
            continue

        state = resolve_service_financial_enforcement_state(
            service,
            as_of=current,
            client_setting=client_setting,
            global_setting=global_setting,
        )
        states.append(state)
        old_status = service.status
        provisioning_result = None

        # Operators can freeze auto-enforcement for selected services.
        if not service.financial_enforcement_override and service.status != "blocked_manual":
            if state["code"] == "suspended_non_payment":
                if service.auto_suspend and service.status in {"active", "pending_payment"}:
                    service.status = "suspended"
                    _ensure_financial_suspension(service, actor=actor, reason="Przekroczono grace period")
                    provisioning_result = _apply_hosting_financial_hook(service, "suspend", actor=actor)
            elif state["code"] in {"overdue", "in_grace_period"}:
                if service.auto_suspend and service.status == "active":
                    service.status = "pending_payment"
            else:
                if _effective_auto_resume(service, client_setting=client_setting, global_setting=global_setting):
                    if service.status in {"pending_payment", "suspended"}:
                        was_suspended = service.status == "suspended"
                        service.status = "active"
                        if was_suspended:
                            _release_financial_suspensions(service)
                            provisioning_result = _apply_hosting_financial_hook(service, "resume", actor=actor)

        if service.status == "suspended" and state["code"] == "suspended_non_payment":
            _ensure_financial_suspension(service, actor=actor, reason="Brak platnosci")

        if old_status != service.status:
            transitions += 1
            _log_service_transition(
                service,
                old_status=old_status,
                new_status=service.status,
                state=state,
                actor=actor,
                provisioning_result=provisioning_result,
            )

    client.billing_status = _derive_client_financial_status(client, states)

    if client.user is not None and client.user.status != "blocked_manual":
        if client.billing_status == "suspended_non_payment":
            client.user.status = "suspended_financial"
        elif client.billing_status in {"overdue", "in_grace_period"}:
            client.user.status = "overdue"
        elif client.billing_status in {"current", "manually_suspended"} and client.user.status in {"overdue", "suspended_financial"}:
            client.user.status = "active"

    if previous_user_status != (client.user.status if client.user else None) and client.user is not None:
        db.session.add(
            UserStatusHistory(
                user=client.user,
                old_status=previous_user_status,
                new_status=client.user.status,
                changed_by=actor,
                reason="Automatyczna egzekucja finansowa",
            )
        )

    if previous_billing_status != client.billing_status:
        log_activity(
            "billing.client_financial_state",
            "client",
            f"Zmiana stanu finansowego klienta {client.user.username if client.user else client.id}: {previous_billing_status} -> {client.billing_status}",
            entity_id=client.id,
            client=client,
            actor=actor,
            metadata={
                "old_billing_status": previous_billing_status,
                "new_billing_status": client.billing_status,
                "settled_overdue_cycles": settled_cycles,
                "service_transitions": transitions,
            },
        )

        # Lazy import to avoid service circular dependencies.
        from panel.services.webhooks import dispatch_webhook_event

        if previous_billing_status != "suspended_non_payment" and client.billing_status == "suspended_non_payment":
            dispatch_webhook_event(
                "billing.suspended",
                {
                    "client_id": client.id,
                    "username": client.user.username if client.user else None,
                    "billing_status": client.billing_status,
                    "service_transitions": transitions,
                },
                client=client,
                auto_commit=False,
            )
        elif previous_billing_status == "suspended_non_payment" and client.billing_status != "suspended_non_payment":
            dispatch_webhook_event(
                "billing.resumed",
                {
                    "client_id": client.id,
                    "username": client.user.username if client.user else None,
                    "billing_status": client.billing_status,
                    "service_transitions": transitions,
                },
                client=client,
                auto_commit=False,
            )

    return {
        "client_id": client.id,
        "service_transitions": transitions,
        "settled_overdue_cycles": settled_cycles,
        "billing_status_changed": previous_billing_status != client.billing_status,
        "user_status_changed": previous_user_status != (client.user.status if client.user else None),
        "billing_status": client.billing_status,
    }


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


def run_financial_enforcement(*, actor: User | None = None, as_of: date | None = None) -> dict:
    current = as_of or date.today()
    clients = Client.query.order_by(Client.id.asc()).all()
    changed_clients = 0
    service_transitions = 0
    for client in clients:
        result = update_client_financial_status_for_date(client, actor=actor, as_of=current)
        service_transitions += int(result["service_transitions"])
        if result["billing_status_changed"] or result["user_status_changed"] or result["service_transitions"]:
            changed_clients += 1
    return {
        "clients": len(clients),
        "changed_clients": changed_clients,
        "service_transitions": service_transitions,
    }


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
        BillingCycle.status == "scheduled",
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
    run_financial_enforcement(actor=actor, as_of=date.today())
    if bool(current_app.config.get("BILLING_OVERDUE_REMINDERS_ENABLED", True)):
        from panel.services.overdue_reminders import send_overdue_reminders

        send_overdue_reminders(actor=actor, as_of=date.today())
    return processed
