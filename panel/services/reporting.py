from __future__ import annotations

from decimal import Decimal

from panel.models import BillingTransaction, Client, ClientService


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _monthly_equivalent(service: ClientService) -> Decimal:
    amount = _to_decimal(service.recurring_amount)
    period = (service.billing_period or "monthly").strip().lower()
    if period == "yearly":
        return (amount / Decimal("12")).quantize(Decimal("0.01"))
    if period == "daily":
        return (amount * Decimal("30")).quantize(Decimal("0.01"))
    return amount


def financial_metrics() -> dict[str, Decimal | int | float]:
    active_services = ClientService.query.filter(ClientService.status.in_(["active", "overdue"])).all()
    mrr = sum((_monthly_equivalent(service) for service in active_services), Decimal("0.00"))

    active_clients = (
        Client.query.filter(Client.billing_status.in_(["current", "overdue", "in_grace_period", "suspended_financial"]))
        .count()
    )
    churned_clients = Client.query.filter(Client.billing_status.in_(["suspended_non_payment", "manually_suspended"])).count()

    arpu = (mrr / Decimal(active_clients)).quantize(Decimal("0.01")) if active_clients else Decimal("0.00")
    churn_rate = round((churned_clients / active_clients) * 100, 2) if active_clients else 0.0

    overdue_transactions = BillingTransaction.query.filter(BillingTransaction.amount < 0).all()
    overdue_amount = sum((abs(_to_decimal(tx.amount)) for tx in overdue_transactions), Decimal("0.00"))

    return {
        "mrr": mrr.quantize(Decimal("0.01")),
        "arpu": arpu,
        "churn_rate": churn_rate,
        "active_clients": active_clients,
        "churned_clients": churned_clients,
        "overdue_amount": overdue_amount.quantize(Decimal("0.01")),
        "overdue_transactions": len(overdue_transactions),
    }
