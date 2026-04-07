from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from panel.extensions import db
from panel.forms.admin import AppearanceSettingsForm, BalanceAdjustmentForm, PasswordResetForm, UserForm
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
from panel.services.smoketest import run_app_smoke_test, write_smoke_test_log
from panel.services.settings import (
    CSS_FRAMEWORK_SETTING_KEY,
    css_framework_choices,
    get_css_framework_key,
    set_setting,
)
from panel.utils.decorators import roles_required


admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _safe_count(query, default: int = 0) -> int:
    try:
        return query.count()
    except SQLAlchemyError:
        return default


def _safe_scalar(query, default=0):
    try:
        value = query.scalar()
        return default if value is None else value
    except SQLAlchemyError:
        return default


def _safe_all(query, default=None):
    if default is None:
        default = []
    try:
        return query.all()
    except SQLAlchemyError:
        return default


@admin_bp.route("/")
@login_required
@roles_required("administrator")
def dashboard():
    try:
        metrics = collect_server_metrics()
    except Exception:
        metrics = []
    stats = {
        "clients": _safe_count(Client.query),
        "domains": _safe_count(Domain.query),
        "subdomains": _safe_count(Subdomain.query),
        "databases": _safe_count(HostingDatabase.query),
        "ftp_accounts": _safe_count(FTPAccount.query),
        "mailboxes": _safe_count(Mailbox.query),
        "overdue_clients": _safe_count(Client.query.filter(Client.billing_status.in_(["overdue", "in_grace_period"]))),
        "suspended_clients": _safe_count(Client.query.filter(Client.billing_status.in_(["suspended_non_payment", "manually_suspended"]))),
        "receivables": _safe_scalar(
            db.session.query(func.coalesce(func.sum(-BillingTransaction.amount), 0)).filter(BillingTransaction.amount < 0),
            0,
        ),
    }
    recent_logs = _safe_all(ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(10))
    recent_finance = _safe_all(BillingTransaction.query.order_by(BillingTransaction.created_at.desc()).limit(10))
    recent_suspensions = _safe_all(UserStatusHistory.query.order_by(UserStatusHistory.created_at.desc()).limit(10))
    try:
        states = service_statuses()
    except Exception:
        states = {}
    return render_template(
        "admin/dashboard.html",
        metrics=metrics,
        stats=stats,
        recent_logs=recent_logs,
        recent_finance=recent_finance,
        recent_suspensions=recent_suspensions,
        service_states=states,
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


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def settings():
    form = AppearanceSettingsForm()
    form.css_framework.choices = css_framework_choices()
    if request.method == "GET":
        form.css_framework.data = get_css_framework_key()
    if form.validate_on_submit():
        set_setting(
            CSS_FRAMEWORK_SETTING_KEY,
            form.css_framework.data,
            "Wybrany framework CSS panelu",
        )
        log_activity(
            "admin.settings_update",
            "system_setting",
            f"Zmieniono framework CSS panelu na {form.css_framework.data}",
            entity_id=CSS_FRAMEWORK_SETTING_KEY,
        )
        db.session.commit()
        flash("Ustawienia wygladu zostaly zapisane.", "success")
        return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", form=form, title="Ustawienia")


@admin_bp.route("/smoke-test", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def smoke_test():
    result = None
    if request.method == "POST":
        result = run_app_smoke_test()
        log_error = write_smoke_test_log(result, source="admin_panel")
        level = "success" if result.success else "warning"
        flash(
            f"Smoketest zakonczony: {result.passed}/{result.total} kontroli zaliczone, czas {result.duration_ms} ms.",
            level,
        )
        if log_error:
            flash(f"Nie udalo sie zapisac logu smoketestu: {log_error}", "warning")
        metadata = result.as_dict()
        if log_error:
            metadata["log_error"] = log_error
        log_activity(
            "admin.smoke_test",
            "application",
            "Uruchomiono smoketest aplikacji z panelu administratora.",
            entity_id="smoke-test",
            actor=current_user,
            metadata=metadata,
            success=result.success,
        )
        db.session.commit()
    return render_template("admin/smoke_test.html", title="Smoketest aplikacji", result=result)
