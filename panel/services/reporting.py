from __future__ import annotations

from datetime import date
from decimal import Decimal

from panel.models import BillingTransaction, Client, ClientService
from panel.services.overdue_reminders import overdue_reminder_stats


def _to_decimal(value) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _shift_month(base: date, delta: int) -> date:
    year = base.year
    month = base.month + delta
    while month < 1:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    return date(year, month, 1)


def _month_key(value: date) -> str:
    return f"{value.year}-{value.month:02d}"


def _monthly_equivalent(service: ClientService) -> Decimal:
    amount = _to_decimal(service.recurring_amount)
    period = (service.billing_period or "monthly").strip().lower()
    if period == "yearly":
        return (amount / Decimal("12")).quantize(Decimal("0.01"))
    if period == "daily":
        return (amount * Decimal("30")).quantize(Decimal("0.01"))
    return amount


def _historical_revenue(*, months: int = 6) -> list[dict[str, Decimal | str]]:
    window_months = max(3, int(months))
    current_month = _month_start(date.today())
    month_starts = [_shift_month(current_month, -offset) for offset in range(window_months - 1, -1, -1)]
    month_keys = [_month_key(item) for item in month_starts]
    totals = {key: Decimal("0.00") for key in month_keys}

    rows = (
        BillingTransaction.query.filter(BillingTransaction.transaction_type == "service_charge")
        .filter(BillingTransaction.created_at >= month_starts[0])
        .all()
    )
    for row in rows:
        if row.created_at is None:
            continue
        amount = _to_decimal(row.amount)
        revenue = abs(amount) if amount < 0 else Decimal("0.00")
        key = _month_key(_month_start(row.created_at.date()))
        if key in totals:
            totals[key] = (totals[key] + revenue).quantize(Decimal("0.01"))

    return [{"month": key, "revenue": totals[key]} for key in month_keys]


def _forecast_next_months(history: list[dict[str, Decimal | str]], *, count: int = 3) -> tuple[list[Decimal], str]:
    values = [
        _to_decimal(item["revenue"])
        for item in history
        if isinstance(item, dict) and "revenue" in item
    ]
    if not values:
        return [Decimal("0.00") for _ in range(count)], "low"

    recent = values[-3:] if len(values) >= 3 else values
    weights = list(range(1, len(recent) + 1))
    weighted_sum = sum((value * Decimal(weight) for value, weight in zip(recent, weights)), Decimal("0.00"))
    denominator = Decimal(sum(weights)) if weights else Decimal("1")
    base = (weighted_sum / denominator).quantize(Decimal("0.01")) if denominator > 0 else values[-1]

    if len(recent) > 1:
        trend = ((recent[-1] - recent[0]) / Decimal(len(recent) - 1)).quantize(Decimal("0.01"))
    else:
        trend = Decimal("0.00")

    forecast: list[Decimal] = []
    for step in range(1, count + 1):
        projected = base + (trend * Decimal(step))
        forecast.append(max(projected, Decimal("0.00")).quantize(Decimal("0.01")))

    non_zero = [value for value in values if value > 0]
    confidence = "low"
    if len(non_zero) >= 3:
        confidence = "medium"
    if len(non_zero) >= 6:
        confidence = "high"
    return forecast, confidence


def financial_metrics() -> dict[str, object]:
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

    history = _historical_revenue(months=6)
    forecast_values, forecast_confidence = _forecast_next_months(history, count=3)
    forecast_month_starts = [_shift_month(_month_start(date.today()), offset) for offset in range(1, 4)]
    forecast_months = [
        {
            "month": _month_key(month_start),
            "revenue": forecast_values[idx],
        }
        for idx, month_start in enumerate(forecast_month_starts)
    ]

    reminder_stats = overdue_reminder_stats(days=30)
    forecast_total = sum(forecast_values, Decimal("0.00")).quantize(Decimal("0.01"))

    return {
        "mrr": mrr.quantize(Decimal("0.01")),
        "arpu": arpu,
        "churn_rate": churn_rate,
        "active_clients": active_clients,
        "churned_clients": churned_clients,
        "overdue_amount": overdue_amount.quantize(Decimal("0.01")),
        "overdue_transactions": len(overdue_transactions),
        "forecast_total_3m": forecast_total,
        "forecast_confidence": forecast_confidence,
        "revenue_history": history,
        "forecast_months": forecast_months,
        "overdue_reminders_sent_30d": reminder_stats["sent"],
        "overdue_reminders_failed_30d": reminder_stats["failed"],
    }
