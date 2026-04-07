from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from panel.extensions import csrf, db
from panel.forms.billing import ClientPlanChangeForm, ClientTopupForm
from panel.forms.services import ClientServiceForm, ServicePlanForm
from panel.models import BillingTransaction, Client, ClientService, ExternalBackupTarget, OnlinePayment, ServicePlan
from panel.services.audit import log_activity
from panel.services.billing import (
    adjust_balance,
    change_service_plan_with_proration,
    financial_enforcement_snapshot,
    plan_price_for_period,
    schedule_initial_cycle,
    update_client_financial_status,
)
from panel.services.payments import (
    PaymentProviderError,
    create_checkout_session,
    is_online_payments_enabled,
    is_paid_checkout_session,
    online_payments_provider,
    parse_stripe_webhook_event,
    retrieve_checkout_session,
)
from panel.services.webhooks import dispatch_webhook_event
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, service_plan_choices


billing_bp = Blueprint("billing", __name__)


def _parse_topup_amount(value: str) -> Decimal:
    normalized = (value or "").strip().replace(",", ".")
    if normalized.startswith("+"):
        normalized = normalized[1:]
    amount = Decimal(normalized)
    return amount.quantize(Decimal("0.01"))


def _safe_amount_limit(config_key: str, fallback: str) -> Decimal:
    raw_value = str(current_app.config.get(config_key, fallback) or fallback)
    try:
        value = Decimal(raw_value)
    except InvalidOperation:
        value = Decimal(fallback)
    return value.quantize(Decimal("0.01"))


def _plan_change_choices_for_service(service: ClientService) -> list[tuple[int, str]]:
    choices: list[tuple[int, str]] = []
    plans = ServicePlan.query.filter_by(is_active=True).order_by(ServicePlan.name.asc()).all()
    for plan in plans:
        amount = plan_price_for_period(plan, service.billing_period)
        choices.append((plan.id, f"{plan.name} ({amount} PLN/{service.billing_period})"))
    return choices


def _plan_change_form_for_service(service: ClientService, *, prefix: str) -> ClientPlanChangeForm:
    form = ClientPlanChangeForm(prefix=prefix)
    form.target_plan_id.choices = _plan_change_choices_for_service(service)
    if service.service_plan_id and request.method == "GET":
        form.target_plan_id.data = service.service_plan_id
    return form


def _complete_online_payment(
    payment: OnlinePayment,
    *,
    source: str,
    provider_event_id: str | None = None,
    session_payload: dict | None = None,
) -> bool:
    if payment.status == "completed":
        return False
    if payment.status not in {"pending", "processing"}:
        return False

    metadata = dict(payment.metadata_json or {})
    if provider_event_id:
        if metadata.get("provider_event_id") == provider_event_id:
            return False
        metadata["provider_event_id"] = provider_event_id
    if session_payload:
        metadata["session"] = {
            "id": session_payload.get("id"),
            "payment_status": session_payload.get("payment_status"),
            "amount_total": session_payload.get("amount_total"),
        }

    payment.status = "completed"
    payment.completed_at = datetime.utcnow()
    if provider_event_id:
        payment.provider_event_id = provider_event_id
    payment.metadata_json = metadata

    adjust_balance(
        payment.client,
        payment.amount,
        "topup_online",
        payment.description,
        actor=payment.actor,
        metadata={
            "provider": payment.provider,
            "online_payment_id": payment.id,
            "external_id": payment.external_id,
            "source": source,
        },
    )
    log_activity(
        "billing.online_payment_completed",
        "online_payment",
        f"Zaksiegowano platnosc online #{payment.id}",
        entity_id=payment.id,
        client=payment.client,
        actor=payment.actor,
        metadata={"provider": payment.provider, "amount": str(payment.amount), "source": source},
    )
    dispatch_webhook_event(
        "payment.completed",
        {
            "payment_id": payment.id,
            "client_id": payment.client_id,
            "amount": str(payment.amount),
            "currency": payment.currency,
            "provider": payment.provider,
            "source": source,
            "external_id": payment.external_id,
        },
        client=payment.client,
        auto_commit=False,
    )
    return True


def _service_plan_form_to_model(form: ServicePlanForm, plan: ServicePlan) -> ServicePlan:
    def parse(value: str) -> Decimal:
        return Decimal((value or "0").replace(",", "."))

    def parse_cpu(value: str) -> float | None:
        raw = (value or "").strip()
        if not raw:
            return None
        parsed = Decimal(raw.replace(",", "."))
        if parsed <= 0:
            raise InvalidOperation("CPU must be positive")
        return float(parsed)

    def parse_ram(value: str) -> int | None:
        raw = (value or "").strip()
        if not raw:
            return None
        parsed = int(raw)
        if parsed <= 0:
            raise ValueError("RAM must be positive")
        return parsed

    plan.name = form.name.data
    plan.code = form.code.data
    plan.description = form.description.data
    plan.monthly_price = parse(form.monthly_price.data)
    plan.daily_price = parse(form.daily_price.data or "0")
    plan.yearly_price = parse(form.yearly_price.data or "0")
    plan.grace_days_override = form.grace_days_override.data if form.grace_days_override.data is not None else None
    plan.backup_frequency = form.backup_frequency.data
    plan.backup_restore_points = int(form.backup_restore_points.data or 7)
    plan.backup_retention_days = int(form.backup_retention_days.data or 30)
    plan.backup_storage_target_id = form.backup_storage_target_id.data or None
    limits = dict(plan.limits_json or {})
    cpu_value = parse_cpu(form.cpu_cores.data)
    ram_value = parse_ram(form.ram_mb.data)
    if cpu_value is None:
        limits.pop("cpu_cores", None)
    else:
        limits["cpu_cores"] = cpu_value
    if ram_value is None:
        limits.pop("ram_mb", None)
    else:
        limits["ram_mb"] = ram_value
    plan.limits_json = limits
    return plan


def _populate_plan_form_targets(form: ServicePlanForm) -> None:
    form.backup_storage_target_id.choices = [(0, "Lokalny storage")]
    targets = ExternalBackupTarget.query.order_by(ExternalBackupTarget.name.asc()).all()
    form.backup_storage_target_id.choices.extend((target.id, target.name) for target in targets)


@billing_bp.route("/admin/billing/plans")
@login_required
@roles_required("administrator")
def admin_plans():
    return render_template("billing/admin_plans.html", plans=ServicePlan.query.order_by(ServicePlan.name.asc()).all())


@billing_bp.route("/admin/billing/plans/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_plan_create():
    form = ServicePlanForm()
    _populate_plan_form_targets(form)
    if form.validate_on_submit():
        try:
            plan = _service_plan_form_to_model(form, ServicePlan())
        except (InvalidOperation, ValueError):
            flash("Nieprawidłowy format kwoty.", "danger")
            return render_template("billing/admin_plan_form.html", form=form, title="Nowy plan")
        db.session.add(plan)
        log_activity("billing.plan_create", "service_plan", f"Utworzono plan {plan.name}")
        db.session.commit()
        flash("Plan został zapisany.", "success")
        return redirect(url_for("billing.admin_plans"))
    return render_template("billing/admin_plan_form.html", form=form, title="Nowy plan")


@billing_bp.route("/admin/billing/plans/<int:plan_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_plan_edit(plan_id: int):
    plan = ServicePlan.query.get_or_404(plan_id)
    form = ServicePlanForm(obj=plan)
    _populate_plan_form_targets(form)
    if request.method == "GET":
        limits = dict(plan.limits_json or {})
        form.cpu_cores.data = str(limits.get("cpu_cores", ""))
        form.ram_mb.data = str(limits.get("ram_mb", ""))
        form.grace_days_override.data = plan.grace_days_override
        form.backup_frequency.data = plan.backup_frequency
        form.backup_restore_points.data = plan.backup_restore_points
        form.backup_retention_days.data = plan.backup_retention_days
        form.backup_storage_target_id.data = plan.backup_storage_target_id or 0
    if form.validate_on_submit():
        try:
            _service_plan_form_to_model(form, plan)
        except (InvalidOperation, ValueError):
            flash("Nieprawidłowy format kwoty.", "danger")
            return render_template("billing/admin_plan_form.html", form=form, title=f"Edycja {plan.name}")
        log_activity("billing.plan_edit", "service_plan", f"Zaktualizowano plan {plan.name}", entity_id=plan.id)
        db.session.commit()
        flash("Plan został zaktualizowany.", "success")
        return redirect(url_for("billing.admin_plans"))
    return render_template("billing/admin_plan_form.html", form=form, title=f"Edycja {plan.name}")


def _populate_service_form(form: ClientServiceForm):
    form.client_id.choices = client_choices()
    form.service_plan_id.choices = service_plan_choices()


@billing_bp.route("/admin/billing/services")
@login_required
@roles_required("administrator")
def admin_services():
    services = (
        ClientService.query.options(
            selectinload(ClientService.plan),
            selectinload(ClientService.client),
        )
        .order_by(ClientService.created_at.desc())
        .all()
    )
    enforcement_states = financial_enforcement_snapshot(services)
    stats = {
        "total": len(services),
        "active": sum(1 for service in services if service.status == "active"),
        "pending": sum(1 for service in services if service.status == "pending_payment"),
        "suspended": sum(1 for service in services if service.status in {"suspended", "blocked_manual"}),
        "monthly_revenue": sum(
            service.recurring_amount
            for service in services
            if service.status != "deleted" and service.billing_period == "monthly"
        ),
    }
    return render_template(
        "billing/admin_services.html",
        services=services,
        stats=stats,
        enforcement_states=enforcement_states,
    )


@billing_bp.route("/admin/billing/services/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_service_create():
    form = ClientServiceForm()
    _populate_service_form(form)
    if form.validate_on_submit():
        try:
            recurring_amount = Decimal(form.recurring_amount.data.replace(",", "."))
        except InvalidOperation:
            flash("Nieprawidłowa kwota cykliczna.", "danger")
            return render_template("billing/admin_service_form.html", form=form, title="Nowa usługa")
        client = Client.query.get_or_404(form.client_id.data)
        service = ClientService(
            client=client,
            service_plan_id=form.service_plan_id.data or None,
            name=form.name.data,
            service_type=form.service_type.data,
            billing_period=form.billing_period.data,
            recurring_amount=recurring_amount,
            status=form.status.data,
            starts_on=form.starts_on.data,
            auto_suspend=form.auto_suspend.data,
            auto_resume=form.auto_resume.data,
            financial_enforcement_override=form.financial_enforcement_override.data,
        )
        db.session.add(service)
        db.session.flush()
        schedule_initial_cycle(service)
        log_activity("billing.service_create", "client_service", f"Utworzono usługę {service.name}", entity_id=service.id, client=client)
        db.session.commit()
        flash("Usługa została utworzona.", "success")
        return redirect(url_for("billing.admin_services"))
    return render_template("billing/admin_service_form.html", form=form, title="Nowa usługa")


@billing_bp.route("/admin/billing/services/<int:service_id>/financial-override", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_service_financial_override(service_id: int):
    service = ClientService.query.get_or_404(service_id)
    enabled = request.form.get("enabled") == "1"
    old_value = bool(service.financial_enforcement_override)
    service.financial_enforcement_override = enabled
    if old_value != enabled:
        log_activity(
            "billing.financial_override",
            "client_service",
            f"Ustawiono financial override dla uslugi {service.name}: {old_value} -> {enabled}",
            entity_id=service.id,
            client=service.client,
            actor=current_user,
            metadata={"old": old_value, "new": enabled},
        )
        db.session.commit()
        flash("Zmieniono ustawienie manualnego override egzekucji finansowej.", "success")
    else:
        flash("Ustawienie override nie zostalo zmienione.", "info")
    return redirect(url_for("billing.admin_services"))


@billing_bp.route("/admin/billing/services/<int:service_id>/manual-suspend", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_service_manual_suspend(service_id: int):
    service = ClientService.query.get_or_404(service_id)
    reason = (request.form.get("reason") or "Reczne zawieszenie przez operatora").strip()[:255]
    if service.status != "blocked_manual":
        old_status = service.status
        service.status = "blocked_manual"
        service.manual_lock_reason = reason
        log_activity(
            "billing.manual_suspend",
            "client_service",
            f"Recznie zawieszono usluge {service.name}",
            entity_id=service.id,
            client=service.client,
            actor=current_user,
            metadata={"old_status": old_status, "new_status": service.status, "reason": reason},
        )
        update_client_financial_status(service.client, actor=current_user)
        db.session.commit()
        flash("Usluga zostala recznie zawieszona.", "warning")
    else:
        flash("Usluga jest juz recznie zawieszona.", "info")
    return redirect(url_for("billing.admin_services"))


@billing_bp.route("/admin/billing/services/<int:service_id>/manual-unsuspend", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_service_manual_unsuspend(service_id: int):
    service = ClientService.query.get_or_404(service_id)
    if service.status == "blocked_manual":
        old_status = service.status
        service.status = "active"
        service.manual_lock_reason = None
        log_activity(
            "billing.manual_unsuspend",
            "client_service",
            f"Recznie odwieszono usluge {service.name}",
            entity_id=service.id,
            client=service.client,
            actor=current_user,
            metadata={"old_status": old_status, "new_status": service.status},
        )
        update_client_financial_status(service.client, actor=current_user)
        db.session.commit()
        flash("Usluga zostala recznie odwieszona.", "success")
    else:
        flash("Usluga nie jest recznie zawieszona.", "info")
    return redirect(url_for("billing.admin_services"))


@billing_bp.route("/admin/billing/transactions")
@login_required
@roles_required("administrator")
def admin_transactions():
    transactions = BillingTransaction.query.order_by(BillingTransaction.created_at.desc()).all()
    return render_template("billing/admin_transactions.html", transactions=transactions)


@billing_bp.route("/client/billing")
@login_required
@roles_required("client")
@active_account_required
def client_billing():
    client = current_client()
    transactions = BillingTransaction.query.filter_by(client_id=client.id).order_by(BillingTransaction.created_at.desc()).all()
    topup_form = ClientTopupForm()
    hosting_services = (
        ClientService.query.filter_by(client_id=client.id, service_type="hosting")
        .filter(ClientService.status != "deleted")
        .order_by(ClientService.created_at.desc())
        .all()
    )
    enforcement_states = financial_enforcement_snapshot(hosting_services)
    plan_change_forms: dict[int, ClientPlanChangeForm] = {}
    for service in hosting_services:
        plan_change_forms[service.id] = _plan_change_form_for_service(service, prefix=f"plan-{service.id}")

    payments_enabled = is_online_payments_enabled()
    recent_online_payments = []
    if payments_enabled:
        recent_online_payments = (
            OnlinePayment.query.filter_by(client_id=client.id)
            .order_by(OnlinePayment.created_at.desc())
            .limit(10)
            .all()
        )
    return render_template(
        "billing/client_billing.html",
        client=client,
        transactions=transactions,
        topup_form=topup_form,
        hosting_services=hosting_services,
        enforcement_states=enforcement_states,
        plan_change_forms=plan_change_forms,
        online_payments_enabled=payments_enabled,
        online_payments_provider=online_payments_provider(),
        recent_online_payments=recent_online_payments,
    )


@billing_bp.route("/client/billing/services/<int:service_id>/plan-change", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_change_plan(service_id: int):
    client = current_client()
    service = ClientService.query.get_or_404(service_id)
    if service.client_id != client.id:
        abort(404)
    if service.service_type != "hosting":
        flash("Zmiana planu jest dostepna tylko dla uslug hostingowych.", "warning")
        return redirect(url_for("billing.client_billing"))

    form = _plan_change_form_for_service(service, prefix=f"plan-{service.id}")
    if not form.validate_on_submit():
        flash("Nieprawidlowe dane zmiany planu.", "danger")
        return redirect(url_for("billing.client_billing"))

    new_plan = ServicePlan.query.get_or_404(form.target_plan_id.data)
    summary = change_service_plan_with_proration(service, new_plan, actor=current_user)
    if not summary.get("changed"):
        flash("Wybrany plan jest juz przypisany do tej uslugi.", "info")
        return redirect(url_for("billing.client_billing"))

    db.session.commit()
    dispatch_webhook_event(
        "service.plan_changed",
        {
            "service_id": service.id,
            "service_name": service.name,
            "billing_period": service.billing_period,
            "old_plan_id": summary.get("old_plan_id"),
            "new_plan_id": summary.get("new_plan_id"),
            "old_amount": str(summary.get("old_amount", "0.00")),
            "new_amount": str(summary.get("new_amount", "0.00")),
            "balance_delta": str(summary.get("balance_delta", "0.00")),
            "remaining_days": summary.get("remaining_days"),
            "cycle_days": summary.get("cycle_days"),
        },
        client=client,
    )

    balance_delta = Decimal(str(summary.get("balance_delta", "0.00")))
    if balance_delta < 0:
        flash(f"Plan uslugi zostal zmieniony. Naliczono doplate {abs(balance_delta)} PLN.", "success")
    elif balance_delta > 0:
        flash(f"Plan uslugi zostal zmieniony. Przyznano zwrot {balance_delta} PLN.", "success")
    else:
        flash("Plan uslugi zostal zmieniony bez doplaty i zwrotu.", "success")
    return redirect(url_for("billing.client_billing"))


@billing_bp.route("/client/billing/topup", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_topup_checkout():
    client = current_client()
    form = ClientTopupForm()
    if not is_online_payments_enabled():
        flash("Platnosci online sa aktualnie wylaczone.", "warning")
        return redirect(url_for("billing.client_billing"))
    if not form.validate_on_submit():
        flash("Nieprawidlowa kwota doladowania.", "danger")
        return redirect(url_for("billing.client_billing"))

    try:
        amount = _parse_topup_amount(form.amount.data)
    except InvalidOperation:
        flash("Nieprawidlowa kwota doladowania.", "danger")
        return redirect(url_for("billing.client_billing"))

    min_amount = _safe_amount_limit("ONLINE_PAYMENTS_MIN_AMOUNT", "5.00")
    max_amount = _safe_amount_limit("ONLINE_PAYMENTS_MAX_AMOUNT", "50000.00")
    if amount < min_amount or amount > max_amount:
        flash(f"Kwota doladowania musi byc w zakresie {min_amount} - {max_amount}.", "warning")
        return redirect(url_for("billing.client_billing"))

    currency = str(current_app.config.get("ONLINE_PAYMENTS_CURRENCY", "PLN") or "PLN").strip().upper()[:8]
    provider = online_payments_provider()
    payment = OnlinePayment(
        client=client,
        actor=current_user,
        amount=amount,
        currency=currency,
        provider=provider,
        status="pending",
        description=f"Doladowanie salda online ({amount} {currency})",
        metadata_json={},
    )
    db.session.add(payment)
    db.session.flush()

    try:
        checkout_data = create_checkout_session(payment, client)
    except PaymentProviderError as exc:
        db.session.rollback()
        flash(f"Nie udalo sie utworzyc sesji platnosci: {exc}", "danger")
        return redirect(url_for("billing.client_billing"))

    payment.external_id = checkout_data.external_id or payment.external_id
    payment.metadata_json = {"checkout_url": checkout_data.checkout_url}
    log_activity(
        "billing.online_payment_checkout",
        "online_payment",
        f"Rozpoczeto platnosc online #{payment.id}",
        entity_id=payment.id,
        client=client,
        actor=current_user,
        metadata={"provider": payment.provider, "amount": str(payment.amount), "currency": payment.currency},
    )
    db.session.commit()
    return redirect(checkout_data.checkout_url)


@billing_bp.route("/client/billing/topup/return")
@login_required
@roles_required("client")
@active_account_required
def client_topup_return():
    client = current_client()
    checkout_session_id = (request.args.get("checkout_session_id") or "").strip()
    if request.args.get("canceled"):
        if checkout_session_id:
            payment = OnlinePayment.query.filter_by(client_id=client.id, external_id=checkout_session_id).first()
            if payment and payment.status == "pending":
                payment.status = "canceled"
                db.session.commit()
        flash("Platnosc online zostala anulowana.", "info")
        return redirect(url_for("billing.client_billing"))

    if not checkout_session_id:
        flash("Brak identyfikatora sesji platnosci.", "warning")
        return redirect(url_for("billing.client_billing"))

    payment = OnlinePayment.query.filter_by(client_id=client.id, external_id=checkout_session_id).first()
    if payment is None:
        flash("Nie znaleziono sesji platnosci dla tego konta.", "warning")
        return redirect(url_for("billing.client_billing"))

    if payment.status == "completed":
        flash("Platnosc zostala juz zaksiegowana.", "success")
        return redirect(url_for("billing.client_billing"))

    if payment.provider == "mock":
        if _complete_online_payment(payment, source="mock_return"):
            db.session.commit()
        flash("Platnosc zostala zaksiegowana.", "success")
        return redirect(url_for("billing.client_billing"))

    if payment.provider != "stripe":
        flash("Nieobslugiwany provider platnosci.", "danger")
        return redirect(url_for("billing.client_billing"))

    try:
        session_payload = retrieve_checkout_session(checkout_session_id, provider="stripe")
    except PaymentProviderError as exc:
        flash(f"Nie udalo sie potwierdzic platnosci: {exc}", "warning")
        return redirect(url_for("billing.client_billing"))

    if not is_paid_checkout_session(session_payload):
        flash("Platnosc nie zostala jeszcze potwierdzona. Sprobuj ponownie za chwile.", "info")
        return redirect(url_for("billing.client_billing"))

    if _complete_online_payment(payment, source="client_return", session_payload=session_payload):
        db.session.commit()
    flash("Platnosc zostala zaksiegowana.", "success")
    return redirect(url_for("billing.client_billing"))


@billing_bp.route("/client/billing/topup/mock-success/<int:payment_id>")
@login_required
@roles_required("client")
@active_account_required
def client_topup_mock_success(payment_id: int):
    if online_payments_provider() != "mock":
        abort(404)
    client = current_client()
    payment = OnlinePayment.query.get_or_404(payment_id)
    if payment.client_id != client.id:
        abort(404)
    if _complete_online_payment(payment, source="mock_success"):
        db.session.commit()
    flash("Platnosc testowa zostala zaksiegowana.", "success")
    return redirect(url_for("billing.client_billing"))


@billing_bp.route("/webhooks/stripe", methods=["POST"])
@csrf.exempt
def stripe_webhook():
    if not is_online_payments_enabled() or online_payments_provider() != "stripe":
        abort(404)

    payload = request.get_data()
    signature = (request.headers.get("Stripe-Signature") or "").strip()
    try:
        event = parse_stripe_webhook_event(payload, signature)
    except PaymentProviderError as exc:
        return {"error": str(exc)}, 400

    event_type = (event.get("type") or "").strip()
    if event_type != "checkout.session.completed":
        return {"received": True}, 200

    event_id = (event.get("id") or "").strip() or None
    session_payload = ((event.get("data") or {}).get("object") or {})
    checkout_session_id = (session_payload.get("id") or "").strip()
    if not checkout_session_id or not is_paid_checkout_session(session_payload):
        return {"received": True}, 200

    payment = OnlinePayment.query.filter_by(external_id=checkout_session_id).first()
    if payment and _complete_online_payment(
        payment,
        source="stripe_webhook",
        provider_event_id=event_id,
        session_payload=session_payload,
    ):
        db.session.commit()

    return {"received": True}, 200
