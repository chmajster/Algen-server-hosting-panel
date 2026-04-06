from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from panel.extensions import db
from panel.forms.admin import BalanceAdjustmentForm, PasswordResetForm, UserForm
from panel.models import (
    ActivityLog,
    BillingTransaction,
    Client,
    Domain,
    FTPAccount,
    HostingDatabase,
    Mailbox,
    Role,
    Subdomain,
    User,
    UserStatusHistory,
)
from panel.services.audit import log_activity
from panel.services.billing import adjust_balance, ensure_client_balance
from panel.services.monitoring import collect_server_metrics, service_statuses
from panel.utils.decorators import roles_required


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


@admin_bp.route("/")
@login_required
@roles_required("administrator")
def dashboard():
    metrics = collect_server_metrics()
    stats = {
        "clients": Client.query.count(),
        "domains": Domain.query.count(),
        "subdomains": Subdomain.query.count(),
        "databases": HostingDatabase.query.count(),
        "ftp_accounts": FTPAccount.query.count(),
        "mailboxes": Mailbox.query.count(),
        "overdue_clients": Client.query.filter_by(billing_status="overdue").count(),
        "suspended_clients": User.query.filter(User.status.in_(["suspended_financial", "blocked_manual"])).count(),
        "receivables": db.session.query(func.coalesce(func.sum(-BillingTransaction.amount), 0))
        .filter(BillingTransaction.amount < 0)
        .scalar(),
    }
    recent_logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(10).all()
    recent_finance = BillingTransaction.query.order_by(BillingTransaction.created_at.desc()).limit(10).all()
    recent_suspensions = UserStatusHistory.query.order_by(UserStatusHistory.created_at.desc()).limit(10).all()
    return render_template(
        "admin/dashboard.html",
        metrics=metrics,
        stats=stats,
        recent_logs=recent_logs,
        recent_finance=recent_finance,
        recent_suspensions=recent_suspensions,
        service_states=service_statuses(),
    )


def _user_form_to_model(form: UserForm, user: User) -> User:
    role = Role.query.filter_by(name=form.role.data).first()
    user.role = role
    user.username = form.username.data
    user.email = form.email.data
    user.first_name = form.first_name.data
    user.last_name = form.last_name.data
    old_status = user.status
    user.status = form.status.data
    if form.password.data:
        user.set_password(form.password.data)
    if form.role.data == "client":
        client = user.client_profile or Client(user=user)
        client.company_name = form.company_name.data
        client.phone = form.phone.data
        client.notes = form.notes.data
        client.allow_dns_management = form.allow_dns_management.data
        client.auto_resume_services = form.auto_resume_services.data
        ensure_client_balance(client)
        db.session.add(client)
    if old_status != user.status:
        db.session.add(
            UserStatusHistory(
                user=user,
                old_status=old_status,
                new_status=user.status,
                changed_by=current_user,
                reason="Zmiana przez panel administratora",
            )
        )
    return user


@admin_bp.route("/users")
@login_required
@roles_required("administrator")
def users():
    return render_template("admin/users_list.html", users=User.query.order_by(User.created_at.desc()).all())


@admin_bp.route("/users/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def user_create():
    form = UserForm()
    form.password.validators = [v for v in form.password.validators if v.__class__.__name__ != "Optional"]
    if form.validate_on_submit():
        user = _user_form_to_model(form, User())
        if not form.password.data:
            flash("Hasło jest wymagane dla nowego użytkownika.", "danger")
            return render_template("admin/user_form.html", form=form, title="Nowy użytkownik")
        db.session.add(user)
        log_activity("admin.user_create", "user", f"Utworzono użytkownika {user.username}", entity_id=user.username)
        db.session.commit()
        flash("Użytkownik został utworzony.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, title="Nowy użytkownik")


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def user_edit(user_id: int):
    user = User.query.get_or_404(user_id)
    form = UserForm(obj=user)
    if request.method == "GET":
        form.role.data = user.role.name
        form.status.data = user.status
        if user.client_profile:
            form.company_name.data = user.client_profile.company_name
            form.phone.data = user.client_profile.phone
            form.notes.data = user.client_profile.notes
            form.allow_dns_management.data = user.client_profile.allow_dns_management
            form.auto_resume_services.data = user.client_profile.auto_resume_services
    if form.validate_on_submit():
        _user_form_to_model(form, user)
        log_activity("admin.user_edit", "user", f"Zaktualizowano użytkownika {user.username}", entity_id=user.id)
        db.session.commit()
        flash("Zmiany zapisane.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, title=f"Edycja {user.username}")


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@login_required
@roles_required("administrator")
def user_delete(user_id: int):
    user = User.query.get_or_404(user_id)
    username = user.username
    db.session.delete(user)
    log_activity("admin.user_delete", "user", f"Usunięto użytkownika {username}", entity_id=user_id)
    db.session.commit()
    flash("Użytkownik został usunięty.", "warning")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/password", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def user_password(user_id: int):
    user = User.query.get_or_404(user_id)
    form = PasswordResetForm()
    if form.validate_on_submit():
        user.set_password(form.password.data)
        log_activity("admin.user_reset_password", "user", f"Zresetowano hasło użytkownika {user.username}", entity_id=user.id)
        db.session.commit()
        flash("Hasło zostało zresetowane.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/password_form.html", form=form, user=user)


@admin_bp.route("/clients/<int:client_id>/balance", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def client_balance(client_id: int):
    client = Client.query.get_or_404(client_id)
    form = BalanceAdjustmentForm()
    if form.validate_on_submit():
        try:
            amount = Decimal(form.amount.data.replace(",", "."))
        except InvalidOperation:
            flash("Nieprawidłowa kwota.", "danger")
            return render_template("admin/client_balance.html", form=form, client=client)
        if form.transaction_type.data in {"deduction", "manual_fee"} and amount > 0:
            amount = -amount
        adjust_balance(
            client,
            amount,
            form.transaction_type.data,
            form.description.data,
            actor=current_user,
        )
        db.session.commit()
        flash("Operacja została zaksięgowana.", "success")
        return redirect(url_for("admin.client_balance", client_id=client.id))
    transactions = BillingTransaction.query.filter_by(client_id=client.id).order_by(BillingTransaction.created_at.desc()).all()
    return render_template("admin/client_balance.html", form=form, client=client, transactions=transactions)
