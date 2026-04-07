from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from panel.extensions import csrf, db
from panel.forms.billing import ClientTopupForm
from panel.forms.services import ClientServiceForm, ServicePlanForm
from panel.models import BillingTransaction, Client, ClientService, OnlinePayment, ServicePlan
from panel.services.audit import log_activity
from panel.services.billing import adjust_balance, schedule_initial_cycle
from panel.services.payments import (
    PaymentProviderError,
    create_checkout_session,
    is_online_payments_enabled,
    is_paid_checkout_session,
    online_payments_provider,
    parse_stripe_webhook_event,
    retrieve_checkout_session,
)
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
    return True


def _service_plan_form_to_model(form: ServicePlanForm, plan: ServicePlan) -> ServicePlan:
    def parse(value: str) -> Decimal:
        return Decimal((value or "0").replace(",", "."))

    plan.name = form.name.data
    plan.code = form.code.data
    plan.description = form.description.data
    plan.monthly_price = parse(form.monthly_price.data)
    plan.daily_price = parse(form.daily_price.data or "0")
    plan.yearly_price = parse(form.yearly_price.data or "0")
    return plan


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
    if form.validate_on_submit():
        try:
            plan = _service_plan_form_to_model(form, ServicePlan())
        except InvalidOperation:
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
    if form.validate_on_submit():
        try:
            _service_plan_form_to_model(form, plan)
        except InvalidOperation:
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
    services = ClientService.query.order_by(ClientService.created_at.desc()).all()
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
    return render_template("billing/admin_services.html", services=services, stats=stats)


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
        )
        db.session.add(service)
        db.session.flush()
        schedule_initial_cycle(service)
        log_activity("billing.service_create", "client_service", f"Utworzono usługę {service.name}", entity_id=service.id, client=client)
        db.session.commit()
        flash("Usługa została utworzona.", "success")
        return redirect(url_for("billing.admin_services"))
    return render_template("billing/admin_service_form.html", form=form, title="Nowa usługa")


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
        online_payments_enabled=payments_enabled,
        online_payments_provider=online_payments_provider(),
        recent_online_payments=recent_online_payments,
    )


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
