from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO

from flask import Blueprint, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from panel.extensions import db
from panel.forms.admin import AppearanceSettingsForm, BalanceAdjustmentForm, PasswordResetForm, UserForm
from panel.forms.automations import AutomationManualTriggerForm, AutomationRuleForm
from panel.models import (
    ActivityLog,
    ApprovalRequest,
    AutomationExecution,
    AutomationRule,
    BillingTransaction,
    BulkOperation,
    Client,
    Domain,
    FTPAccount,
    HostingDatabase,
    Mailbox,
    MigrationJob,
    OperatorPermission,
    ExportJob,
    RegistrationFraudCheck,
    Role,
    Subdomain,
    User,
    UserStatusHistory,
)
from panel.services.anti_fraud import mark_fraud_check_reviewed
from panel.services.approvals import ApprovalDecisionError, decide_approval_request, expire_due_approval_requests
from panel.services.audit import log_activity, verify_activity_chain
from panel.services.billing import adjust_balance, ensure_client_balance
from panel.services.monitoring import collect_server_metrics, service_statuses
from panel.services.migrations import cancel_migration_job, run_due_migration_jobs
from panel.services.overdue_reminders import send_overdue_reminders
from panel.services.operator_permissions import domain_choices, has_custom_permissions, permissions_matrix, save_permissions_matrix
from panel.services.smoketest import run_app_smoke_test, write_smoke_test_log
from panel.services.automations import execute_automation_rules, parse_json_text
from panel.services.bulk_operations import bulk_lock_user_accounts, bulk_update_client_limits
from panel.services.exports import (
    create_export_job,
    dataset_compliance,
    dataset_compliance_controls,
    dataset_clients,
    dataset_dr_readiness,
    dataset_events,
    dataset_invoices,
    dataset_resource_usage,
    dataset_tickets,
    serialize_csv,
    serialize_xlsx,
)
from panel.services.reporting import financial_metrics
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


def _automation_form_to_model(form: AutomationRuleForm, rule: AutomationRule) -> tuple[AutomationRule | None, str | None]:
    try:
        conditions = parse_json_text(form.conditions_json.data, default={})
        actions = parse_json_text(form.actions_json.data, default=[])
    except json.JSONDecodeError as exc:
        return None, f"Nieprawidlowy JSON: {exc.msg}"

    if conditions and not isinstance(conditions, dict):
        return None, "Warunki musza byc obiektem JSON."
    if not isinstance(actions, list):
        return None, "Akcje musza byc lista JSON."

    rule.name = (form.name.data or "").strip()
    rule.description = (form.description.data or "").strip() or None
    rule.trigger_event = (form.trigger_event.data or "").strip()
    rule.conditions_json = conditions or {}
    rule.actions_json = actions
    rule.stop_on_match = bool(form.stop_on_match.data)
    rule.is_active = bool(form.is_active.data)
    return rule, None


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
        "fraud_alerts": _safe_count(
            RegistrationFraudCheck.query.filter(RegistrationFraudCheck.risk_level.in_(["medium", "high"]))
            .filter(RegistrationFraudCheck.reviewed_at.is_(None))
        ),
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


@admin_bp.route("/reports")
@login_required
@roles_required("administrator")
def reports():
    metrics = financial_metrics()
    return render_template("admin/reports.html", title="Raporty", metrics=metrics)


@admin_bp.route("/reports/reminders/send", methods=["POST"])
@login_required
@roles_required("administrator")
def reports_send_overdue_reminders():
    summary = send_overdue_reminders(actor=current_user)
    db.session.commit()
    flash(
        "Przypomnienia overdue: "
        f"wyslano={summary['sent']}, bledy={summary['failed']}, pominieto={summary['skipped']}",
        "info",
    )
    return redirect(url_for("admin.reports"))


@admin_bp.route("/security/anti-fraud")
@login_required
@roles_required("administrator")
def anti_fraud():
    risk_filter = (request.args.get("risk") or "all").strip().lower()
    state_filter = (request.args.get("state") or "open").strip().lower()

    query = RegistrationFraudCheck.query.order_by(RegistrationFraudCheck.created_at.desc())
    if risk_filter in {"low", "medium", "high"}:
        query = query.filter(RegistrationFraudCheck.risk_level == risk_filter)

    if state_filter == "open":
        query = query.filter(RegistrationFraudCheck.reviewed_at.is_(None))
    elif state_filter == "reviewed":
        query = query.filter(RegistrationFraudCheck.reviewed_at.is_not(None))
    elif state_filter == "blocked":
        query = query.filter(RegistrationFraudCheck.blocked.is_(True))

    checks = query.limit(250).all()
    return render_template(
        "admin/anti_fraud.html",
        title="Anti-fraud",
        checks=checks,
        risk_filter=risk_filter,
        state_filter=state_filter,
    )


@admin_bp.route("/security/anti-fraud/<int:check_id>/review", methods=["POST"])
@login_required
@roles_required("administrator")
def anti_fraud_review(check_id: int):
    check = RegistrationFraudCheck.query.get_or_404(check_id)
    note = (request.form.get("note") or "").strip()[:255]
    mark_fraud_check_reviewed(check, current_user, note=note)
    log_activity(
        "admin.anti_fraud_review",
        "registration_fraud_check",
        "Zamknieto alert anti-fraud",
        entity_id=check.id,
        actor=current_user,
        metadata={"score": check.score, "risk_level": check.risk_level, "note": note},
    )
    db.session.commit()
    flash("Alert anti-fraud oznaczony jako przejrzany.", "success")
    return redirect(url_for("admin.anti_fraud"))


@admin_bp.route("/security/anti-fraud/<int:check_id>/unlock", methods=["POST"])
@login_required
@roles_required("administrator")
def anti_fraud_unlock(check_id: int):
    check = RegistrationFraudCheck.query.get_or_404(check_id)
    if check.user is None:
        flash("Brak powiazanego konta uzytkownika dla tego alertu.", "warning")
        return redirect(url_for("admin.anti_fraud"))

    check.user.is_active_account = True
    if check.user.status == "inactive":
        check.user.status = "active"
    if check.user.manual_lock_reason and "anti-fraud" in check.user.manual_lock_reason.lower():
        check.user.manual_lock_reason = None

    mark_fraud_check_reviewed(check, current_user, note="Manualny unlock po przegladzie")
    log_activity(
        "admin.anti_fraud_unlock",
        "user",
        f"Odblokowano konto po alarmie anti-fraud: {check.user.username}",
        entity_id=check.user.id,
        actor=current_user,
        metadata={"fraud_check_id": check.id, "score": check.score, "risk_level": check.risk_level},
    )
    db.session.commit()
    flash("Konto zostalo odblokowane po przegladzie anti-fraud.", "success")
    return redirect(url_for("admin.anti_fraud"))


@admin_bp.route("/security/approvals")
@login_required
@roles_required("administrator")
def approvals():
    expire_due_approval_requests(limit=250)

    status_filter = (request.args.get("status") or "pending").strip().lower()
    action_filter = (request.args.get("action") or "all").strip().lower()
    allowed_statuses = {"all", "pending", "approved", "executed", "rejected", "expired", "cancelled"}
    if status_filter not in allowed_statuses:
        status_filter = "pending"

    query = ApprovalRequest.query.order_by(ApprovalRequest.created_at.desc())
    if status_filter != "all":
        query = query.filter(ApprovalRequest.status == status_filter)
    if action_filter != "all":
        query = query.filter(ApprovalRequest.action_key == action_filter)

    requests = query.limit(250).all()
    counts = {
        "pending": _safe_count(ApprovalRequest.query.filter_by(status="pending")),
        "approved": _safe_count(ApprovalRequest.query.filter_by(status="approved")),
        "executed": _safe_count(ApprovalRequest.query.filter_by(status="executed")),
        "rejected": _safe_count(ApprovalRequest.query.filter_by(status="rejected")),
        "expired": _safe_count(ApprovalRequest.query.filter_by(status="expired")),
        "cancelled": _safe_count(ApprovalRequest.query.filter_by(status="cancelled")),
    }
    action_keys = [
        item[0]
        for item in _safe_all(
            db.session.query(ApprovalRequest.action_key).distinct().order_by(ApprovalRequest.action_key.asc()),
            default=[],
        )
    ]
    db.session.commit()
    return render_template(
        "admin/approvals.html",
        title="Akceptacje ryzykownych akcji",
        requests=requests,
        counts=counts,
        status_filter=status_filter,
        action_filter=action_filter,
        action_keys=action_keys,
    )


@admin_bp.route("/security/approvals/<int:request_id>/approve", methods=["POST"])
@login_required
@roles_required("administrator")
def approvals_approve(request_id: int):
    request_obj = ApprovalRequest.query.get_or_404(request_id)
    note = (request.form.get("note") or "").strip()[:255]
    try:
        result = decide_approval_request(
            request_obj=request_obj,
            user=current_user,
            decision="approve",
            note=note,
        )
        db.session.commit()
    except ApprovalDecisionError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("admin.approvals"))
    except Exception as exc:
        db.session.rollback()
        flash(f"Nie udalo sie zapisac decyzji: {exc}", "danger")
        return redirect(url_for("admin.approvals"))

    if result["status"] == "executed":
        flash(f"Wniosek #{request_obj.id} zatwierdzony i wykonany.", "success")
    elif result["status"] == "approved":
        flash(f"Wniosek #{request_obj.id} zatwierdzony.", "success")
    else:
        flash(
            f"Wniosek #{request_obj.id}: zatwierdzenia {result['approvals']}/{result['required_approvals']}.",
            "info",
        )
    return redirect(url_for("admin.approvals"))


@admin_bp.route("/security/approvals/<int:request_id>/reject", methods=["POST"])
@login_required
@roles_required("administrator")
def approvals_reject(request_id: int):
    request_obj = ApprovalRequest.query.get_or_404(request_id)
    note = (request.form.get("note") or "").strip()[:255]
    try:
        decide_approval_request(
            request_obj=request_obj,
            user=current_user,
            decision="reject",
            note=note,
        )
        db.session.commit()
    except ApprovalDecisionError as exc:
        db.session.rollback()
        flash(str(exc), "warning")
        return redirect(url_for("admin.approvals"))
    except Exception as exc:
        db.session.rollback()
        flash(f"Nie udalo sie odrzucic wniosku: {exc}", "danger")
        return redirect(url_for("admin.approvals"))

    flash(f"Wniosek #{request_obj.id} zostal odrzucony.", "warning")
    return redirect(url_for("admin.approvals"))


@admin_bp.route("/security/audit-integrity")
@login_required
@roles_required("administrator")
def audit_integrity():
    result = verify_activity_chain(max_errors=200)
    return render_template(
        "admin/audit_integrity.html",
        title="Integralnosc audytu",
        result=result,
    )


@admin_bp.route("/exports")
@login_required
@roles_required("administrator")
def exports_index():
    jobs = ExportJob.query.order_by(ExportJob.created_at.desc()).limit(200).all()
    return render_template("admin/exports.html", title="Eksport danych", jobs=jobs)


@admin_bp.route("/exports/<string:dataset_key>")
@login_required
@roles_required("administrator")
def export_dataset(dataset_key: str):
    dataset = (dataset_key or "").strip().lower()
    format_name = (request.args.get("format") or "csv").strip().lower()
    if format_name not in {"csv", "xlsx"}:
        format_name = "csv"

    raw_limit = (request.args.get("limit") or "5000").strip()
    limit = 5000
    if raw_limit.isdigit():
        limit = max(1, min(int(raw_limit), 20000))

    filters: dict = {"limit": limit}
    headers: list[str]
    rows: list[list]

    try:
        if dataset == "clients":
            status_filter = (request.args.get("status") or "").strip().lower() or None
            filters["status"] = status_filter
            headers, rows = dataset_clients(status_filter=status_filter, limit=limit)
        elif dataset == "invoices":
            status_filter = (request.args.get("status") or "").strip().lower() or None
            client_id = None
            client_id_raw = (request.args.get("client_id") or "").strip()
            if client_id_raw.isdigit():
                client_id = int(client_id_raw)
            filters["status"] = status_filter
            filters["client_id"] = client_id
            headers, rows = dataset_invoices(status_filter=status_filter, client_id=client_id, limit=limit)
        elif dataset == "tickets":
            status_filter = (request.args.get("status") or "").strip().lower() or None
            category_filter = (request.args.get("category") or "").strip().lower() or None
            filters["status"] = status_filter
            filters["category"] = category_filter
            headers, rows = dataset_tickets(status_filter=status_filter, category_filter=category_filter, limit=limit)
        elif dataset == "resource_usage":
            client_id = None
            client_id_raw = (request.args.get("client_id") or "").strip()
            if client_id_raw.isdigit():
                client_id = int(client_id_raw)
            filters["client_id"] = client_id
            headers, rows = dataset_resource_usage(client_id=client_id, limit=limit)
        elif dataset == "compliance":
            client_id = None
            client_id_raw = (request.args.get("client_id") or "").strip()
            if client_id_raw.isdigit():
                client_id = int(client_id_raw)
            filters["client_id"] = client_id
            headers, rows = dataset_compliance(client_id=client_id, limit=limit)
        elif dataset == "compliance_controls":
            client_id = None
            client_id_raw = (request.args.get("client_id") or "").strip()
            if client_id_raw.isdigit():
                client_id = int(client_id_raw)
            filters["client_id"] = client_id
            headers, rows = dataset_compliance_controls(client_id=client_id, limit=limit)
        elif dataset == "events":
            client_id = None
            client_id_raw = (request.args.get("client_id") or "").strip()
            if client_id_raw.isdigit():
                client_id = int(client_id_raw)
            filters["client_id"] = client_id
            headers, rows = dataset_events(client_id=client_id, limit=limit)
        elif dataset == "dr_readiness":
            client_id = None
            client_id_raw = (request.args.get("client_id") or "").strip()
            if client_id_raw.isdigit():
                client_id = int(client_id_raw)
            filters["client_id"] = client_id
            headers, rows = dataset_dr_readiness(client_id=client_id, limit=limit)
        else:
            flash("Nieznany dataset eksportu.", "danger")
            return redirect(url_for("admin.exports_index"))

        payload: BytesIO
        mimetype: str
        if format_name == "xlsx":
            payload = serialize_xlsx(headers, rows)
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            payload = serialize_csv(headers, rows)
            mimetype = "text/csv; charset=utf-8"

        job = create_export_job(
            dataset=dataset,
            format_name=format_name,
            requested_by=current_user,
            filters=filters,
            row_count=len(rows),
            status="completed",
        )
        log_activity(
            "admin.export_dataset",
            "export_job",
            f"Wyeksportowano dataset {dataset} ({format_name})",
            entity_id=job.id,
            actor=current_user,
            metadata={"dataset": dataset, "format": format_name, "row_count": len(rows), "filters": filters},
        )
        db.session.commit()

        extension = "xlsx" if format_name == "xlsx" else "csv"
        filename = f"{dataset}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.{extension}"
        return send_file(payload, as_attachment=True, download_name=filename, mimetype=mimetype)
    except Exception as exc:
        job = create_export_job(
            dataset=dataset,
            format_name=format_name,
            requested_by=current_user,
            filters=filters,
            row_count=0,
            status="failed",
            error_message=str(exc),
        )
        log_activity(
            "admin.export_dataset_failed",
            "export_job",
            f"Eksport datasetu {dataset} nie powiodl sie",
            entity_id=job.id,
            actor=current_user,
            success=False,
            metadata={"dataset": dataset, "format": format_name, "error": str(exc)[:255]},
        )
        db.session.commit()
        flash(f"Eksport nie powiodl sie: {exc}", "danger")
        return redirect(url_for("admin.exports_index"))


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
    elif user.id is not None:
        OperatorPermission.query.filter_by(user_id=user.id).delete(synchronize_session=False)
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


def _parse_operator_permissions_form() -> tuple[bool, dict[str, dict[str, bool]]]:
    enabled = (request.form.get("granular_enabled") or "") == "1"
    matrix: dict[str, dict[str, bool]] = {}
    for domain in domain_choices():
        can_read = bool(request.form.get(f"{domain.key}_can_read"))
        can_write = bool(request.form.get(f"{domain.key}_can_write"))
        matrix[domain.key] = {
            "can_read": can_read,
            "can_write": can_write,
        }
    return enabled, matrix


@admin_bp.route("/users")
@login_required
@roles_required("administrator")
def users():
    users = User.query.order_by(User.created_at.desc()).all()
    bulk_operation = None
    bulk_items = []
    bulk_operation_id_raw = (request.args.get("bulk_operation_id") or "").strip()
    if bulk_operation_id_raw.isdigit():
        bulk_operation = BulkOperation.query.get(int(bulk_operation_id_raw))
        if bulk_operation is not None and bulk_operation.target_type in {"user", "client"}:
            bulk_items = list(bulk_operation.items)
    return render_template(
        "admin/users_list.html",
        users=users,
        bulk_operation=bulk_operation,
        bulk_items=bulk_items,
    )


@admin_bp.route("/users/bulk-lock", methods=["POST"])
@login_required
@roles_required("administrator")
def users_bulk_lock():
    action = (request.form.get("action") or "lock").strip().lower()
    lock = action != "unlock"
    dry_run = (request.form.get("dry_run") or "") == "1"
    confirm_text = (request.form.get("confirm_text") or "").strip()
    reason = (request.form.get("reason") or "Masowa blokada konta").strip()

    user_ids = []
    for raw_id in request.form.getlist("user_ids"):
        if str(raw_id).isdigit():
            user_ids.append(int(raw_id))

    if not user_ids:
        flash("Wybierz co najmniej jednego uzytkownika.", "warning")
        return redirect(url_for("admin.users"))

    if lock and not dry_run and confirm_text != "POTWIERDZ":
        flash("Dla masowej blokady wpisz POTWIERDZ.", "danger")
        return redirect(url_for("admin.users"))

    operation, summary = bulk_lock_user_accounts(
        user_ids=user_ids,
        reason=reason,
        lock=lock,
        actor=current_user,
        dry_run=dry_run,
    )
    log_activity(
        "admin.users_bulk_lock",
        "bulk_operation",
        "Wykonano masowa operacje na kontach uzytkownikow",
        entity_id=operation.id,
        actor=current_user,
        metadata={"lock": lock, "dry_run": dry_run, "summary": summary},
    )
    db.session.commit()

    flash(
        f"Bulk konta: sukces {summary['success']}, bledy {summary['failed']}, "
        f"tryb {'podglad' if dry_run else 'wykonanie'}.",
        "info",
    )
    return redirect(url_for("admin.users", bulk_operation_id=operation.id))


@admin_bp.route("/users/bulk-limits", methods=["POST"])
@login_required
@roles_required("administrator")
def users_bulk_limits():
    dry_run = (request.form.get("dry_run") or "") == "1"
    disk_raw = (request.form.get("disk_hard_mb") or "").strip()
    inode_raw = (request.form.get("inode_limit") or "").strip()

    disk_hard_mb = None
    inode_limit = None
    try:
        if disk_raw:
            disk_hard_mb = int(disk_raw)
        if inode_raw:
            inode_limit = int(inode_raw)
    except ValueError:
        flash("Limity musza byc liczbami calkowitymi.", "danger")
        return redirect(url_for("admin.users"))

    if disk_hard_mb is None and inode_limit is None:
        flash("Podaj co najmniej jeden limit do aktualizacji.", "warning")
        return redirect(url_for("admin.users"))

    user_ids = []
    for raw_id in request.form.getlist("user_ids"):
        if str(raw_id).isdigit():
            user_ids.append(int(raw_id))

    if not user_ids:
        flash("Wybierz co najmniej jednego uzytkownika.", "warning")
        return redirect(url_for("admin.users"))

    clients = Client.query.filter(Client.user_id.in_(user_ids)).all()
    client_ids = [client.id for client in clients]
    if not client_ids:
        flash("Wybrani uzytkownicy nie maja profilu klienta.", "warning")
        return redirect(url_for("admin.users"))

    operation, summary = bulk_update_client_limits(
        client_ids=client_ids,
        disk_hard_mb=disk_hard_mb,
        inode_limit=inode_limit,
        actor=current_user,
        dry_run=dry_run,
    )
    log_activity(
        "admin.users_bulk_limits",
        "bulk_operation",
        "Wykonano masowa aktualizacje limitow klientow",
        entity_id=operation.id,
        actor=current_user,
        metadata={"dry_run": dry_run, "summary": summary, "disk_hard_mb": disk_hard_mb, "inode_limit": inode_limit},
    )
    db.session.commit()

    flash(
        f"Bulk limity: sukces {summary['success']}, bledy {summary['failed']}, "
        f"tryb {'podglad' if dry_run else 'wykonanie'}.",
        "info",
    )
    return redirect(url_for("admin.users", bulk_operation_id=operation.id))


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


@admin_bp.route("/users/<int:user_id>/permissions", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def user_permissions(user_id: int):
    if not current_user.has_role("administrator"):
        flash("Brak uprawnien do zarzadzania uprawnieniami operatorow.", "danger")
        return redirect(url_for("admin.users"))

    user = User.query.get_or_404(user_id)
    if not user.has_role("operator"):
        flash("Granularne uprawnienia mozna przypisywac tylko operatorom.", "warning")
        return redirect(url_for("admin.user_edit", user_id=user.id))

    if request.method == "POST":
        enabled, matrix = _parse_operator_permissions_form()
        save_permissions_matrix(user=user, enabled=enabled, matrix=matrix)
        log_activity(
            "admin.operator_permissions_update",
            "operator_permission",
            f"Zaktualizowano granularne uprawnienia operatora {user.username}",
            entity_id=user.id,
            actor=current_user,
            metadata={"enabled": enabled, "domains": matrix},
        )
        db.session.commit()
        flash("Uprawnienia operatora zostaly zapisane.", "success")
        return redirect(url_for("admin.user_permissions", user_id=user.id))

    return render_template(
        "admin/operator_permissions.html",
        user=user,
        domains=domain_choices(),
        matrix=permissions_matrix(user),
        granular_enabled=has_custom_permissions(user),
        title=f"Uprawnienia operatora: {user.username}",
    )


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


@admin_bp.route("/migrations")
@login_required
@roles_required("administrator")
def migrations():
    jobs = MigrationJob.query.order_by(MigrationJob.created_at.desc()).limit(200).all()
    return render_template("admin/migrations.html", title="Migracje", jobs=jobs)


@admin_bp.route("/migrations/process", methods=["POST"])
@login_required
@roles_required("administrator")
def migrations_process():
    processed = run_due_migration_jobs(limit=50)
    execute_automation_rules(
        trigger_event="migration.queue_processed",
        payload={"processed": processed},
        actor=current_user,
    )
    log_activity(
        "admin.migrations_process",
        "migration_job",
        "Uruchomiono przetwarzanie kolejek migracji",
        entity_id="migration-queue",
        actor=current_user,
        metadata={"processed": processed},
    )
    db.session.commit()
    flash(f"Przetworzono zgloszen migracji: {processed}.", "success")
    return redirect(url_for("admin.migrations"))


@admin_bp.route("/migrations/<int:job_id>/cancel", methods=["POST"])
@login_required
@roles_required("administrator")
def migration_cancel(job_id: int):
    job = MigrationJob.query.get_or_404(job_id)
    reason = (request.form.get("reason") or "Anulowano przez administratora").strip()
    if cancel_migration_job(job, reason=reason):
        execute_automation_rules(
            trigger_event="migration.job_cancelled_by_admin",
            payload={"job_id": job.id, "client_id": job.client_id, "status": "cancelled"},
            client=job.client,
            actor=current_user,
        )
        log_activity(
            "admin.migration_cancel",
            "migration_job",
            "Administrator anulowal migracje",
            entity_id=job.id,
            actor=current_user,
            client=job.client,
            metadata={"reason": reason[:255]},
        )
        db.session.commit()
        flash("Migracja zostala anulowana.", "info")
    else:
        flash("Tej migracji nie mozna juz anulowac.", "warning")
    return redirect(url_for("admin.migrations"))


@admin_bp.route("/migrations/<int:job_id>/retry", methods=["POST"])
@login_required
@roles_required("administrator")
def migration_retry(job_id: int):
    job = MigrationJob.query.get_or_404(job_id)
    if job.status not in {"failed", "cancelled"}:
        flash("Retry jest dostepne tylko dla statusu failed lub cancelled.", "warning")
        return redirect(url_for("admin.migrations"))

    job.status = "queued"
    job.current_step = "preflight"
    job.progress_percent = 0
    job.started_at = None
    job.finished_at = None
    job.last_error = None
    log_activity(
        "admin.migration_retry",
        "migration_job",
        "Administrator ponownie zakolejkowal migracje",
        entity_id=job.id,
        actor=current_user,
        client=job.client,
    )
    execute_automation_rules(
        trigger_event="migration.job_requeued",
        payload={"job_id": job.id, "client_id": job.client_id, "status": job.status},
        client=job.client,
        actor=current_user,
    )
    db.session.commit()
    flash("Migracja zostala ponownie zakolejkowana.", "success")
    return redirect(url_for("admin.migrations"))


@admin_bp.route("/automations", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def automations():
    trigger_form = AutomationManualTriggerForm()
    if trigger_form.validate_on_submit():
        try:
            payload = parse_json_text(trigger_form.payload_json.data, default={})
        except json.JSONDecodeError as exc:
            flash(f"Nieprawidlowy JSON payload: {exc.msg}", "danger")
            return redirect(url_for("admin.automations"))
        if payload and not isinstance(payload, dict):
            flash("Payload musi byc obiektem JSON.", "danger")
            return redirect(url_for("admin.automations"))

        summary = execute_automation_rules(
            trigger_event=trigger_form.trigger_event.data,
            payload=payload,
            actor=current_user,
        )
        log_activity(
            "admin.automation_trigger_manual",
            "automation_rule",
            "Reczne wyzwolenie automatyzacji",
            entity_id=trigger_form.trigger_event.data,
            actor=current_user,
            metadata=summary,
        )
        db.session.commit()
        flash(
            "Wyzwolono reguly: "
            f"matched={summary['matched']}, executed={summary['executed']}, "
            f"failed={summary['failed']}, skipped={summary['skipped']}",
            "info",
        )
        return redirect(url_for("admin.automations"))

    rules = AutomationRule.query.order_by(AutomationRule.created_at.desc()).all()
    executions = AutomationExecution.query.order_by(AutomationExecution.created_at.desc()).limit(100).all()
    return render_template(
        "admin/automations.html",
        title="Automatyzacje",
        rules=rules,
        executions=executions,
        trigger_form=trigger_form,
    )


@admin_bp.route("/automations/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def automation_create():
    form = AutomationRuleForm()
    if request.method == "GET":
        form.conditions_json.data = "{}"
        form.actions_json.data = "[]"
        form.is_active.data = True

    if form.validate_on_submit():
        rule, error = _automation_form_to_model(form, AutomationRule())
        if error:
            flash(error, "danger")
        else:
            db.session.add(rule)
            log_activity(
                "admin.automation_create",
                "automation_rule",
                f"Utworzono regule automatyzacji {rule.name}",
                entity_id=rule.name,
                actor=current_user,
                metadata={"trigger_event": rule.trigger_event},
            )
            db.session.commit()
            flash("Regula automatyzacji zostala utworzona.", "success")
            return redirect(url_for("admin.automations"))

    return render_template("admin/automation_form.html", title="Nowa regula automatyzacji", form=form)


@admin_bp.route("/automations/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def automation_edit(rule_id: int):
    rule = AutomationRule.query.get_or_404(rule_id)
    form = AutomationRuleForm(obj=rule)
    if request.method == "GET":
        form.conditions_json.data = json.dumps(rule.conditions_json or {}, ensure_ascii=True, indent=2)
        form.actions_json.data = json.dumps(rule.actions_json or [], ensure_ascii=True, indent=2)
        form.is_active.data = bool(rule.is_active)
        form.stop_on_match.data = bool(rule.stop_on_match)

    if form.validate_on_submit():
        updated_rule, error = _automation_form_to_model(form, rule)
        if error:
            flash(error, "danger")
        else:
            log_activity(
                "admin.automation_edit",
                "automation_rule",
                f"Zaktualizowano regule automatyzacji {updated_rule.name}",
                entity_id=updated_rule.id,
                actor=current_user,
                metadata={"trigger_event": updated_rule.trigger_event},
            )
            db.session.commit()
            flash("Regula automatyzacji zostala zaktualizowana.", "success")
            return redirect(url_for("admin.automations"))

    return render_template(
        "admin/automation_form.html",
        title=f"Edycja reguly: {rule.name}",
        form=form,
    )


@admin_bp.route("/automations/<int:rule_id>/delete", methods=["POST"])
@login_required
@roles_required("administrator")
def automation_delete(rule_id: int):
    rule = AutomationRule.query.get_or_404(rule_id)
    rule_name = rule.name
    db.session.delete(rule)
    log_activity(
        "admin.automation_delete",
        "automation_rule",
        f"Usunieto regule automatyzacji {rule_name}",
        entity_id=rule_id,
        actor=current_user,
    )
    db.session.commit()
    flash("Regula automatyzacji zostala usunieta.", "warning")
    return redirect(url_for("admin.automations"))
