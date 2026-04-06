from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.forms.services import ClientServiceForm, ServicePlanForm
from panel.models import BillingTransaction, Client, ClientService, ServicePlan
from panel.services.audit import log_activity
from panel.services.billing import schedule_initial_cycle
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, optionalized, service_plan_choices


billing_bp = Blueprint("billing", __name__)


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
    return render_template("billing/admin_services.html", services=services)


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
    return render_template("billing/client_billing.html", client=client, transactions=transactions)
