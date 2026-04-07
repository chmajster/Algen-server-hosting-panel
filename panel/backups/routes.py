from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.forms.backups import ExternalBackupTargetForm
from panel.forms.services import BackupForm
from panel.models import Backup, BackupRestoreJob, BackupVerificationRun, Client, ExternalBackupTarget
from panel.services.audit import log_activity
from panel.services.backup_restore import create_restore_job, process_restore_job
from panel.services.backup_storage import (
    apply_restore_points_retention,
    resolve_client_backup_policy,
    upload_backup_to_target,
    validate_backup_target_connectivity,
)
from panel.services.backup_verification import run_verification_schedule, verify_backup
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, database_choices, domain_choices


backups_bp = Blueprint("backups", __name__)


def _populate_form(form: BackupForm):
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.domain_id.choices = [(0, "Brak")] + domain_choices(selected_client_id)
    form.database_id.choices = [(0, "Brak")] + database_choices(selected_client_id)
    form.storage_target_id.choices = [(0, "Lokalny")]
    targets = ExternalBackupTarget.query.order_by(ExternalBackupTarget.name.asc()).all()
    form.storage_target_id.choices.extend((target.id, target.name) for target in targets)


@backups_bp.route("/admin/backups")
@login_required
@roles_required("administrator")
def admin_backups():
    backups = Backup.query.order_by(Backup.created_at.desc()).all()
    restore_jobs = BackupRestoreJob.query.order_by(BackupRestoreJob.created_at.desc()).limit(100).all()
    verification_runs = BackupVerificationRun.query.order_by(BackupVerificationRun.created_at.desc()).limit(100).all()
    targets = ExternalBackupTarget.query.order_by(ExternalBackupTarget.created_at.desc()).all()
    return render_template(
        "backups/admin_backups.html",
        backups=backups,
        restore_jobs=restore_jobs,
        verification_runs=verification_runs,
        targets=targets,
    )


@backups_bp.route("/admin/backups/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_create():
    form = BackupForm()
    _populate_form(form)
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        policy = resolve_client_backup_policy(client)
        target = None
        selected_target_id = form.storage_target_id.data or policy.get("storage_target_id")
        if selected_target_id:
            target = ExternalBackupTarget.query.get(selected_target_id)
            if target is None:
                flash("Wybrany storage target nie istnieje.", "danger")
                return render_template("backups/backup_form.html", form=form, title="Nowy backup")

        backup = Backup(
            client=client,
            domain_id=form.domain_id.data or None,
            database_id=form.database_id.data or None,
            backup_type=form.backup_type.data,
            status="scheduled" if form.scheduled_for.data else "queued",
            storage_path=form.storage_path.data,
            scheduled_for=form.scheduled_for.data,
            storage_target=target,
            retention_until=datetime.utcnow() + timedelta(days=max(1, int(policy.get("retention_days", 30)))),
        )
        db.session.add(backup)

        source_path = Path(backup.storage_path)
        if not source_path.is_absolute():
            source_path = Path(current_app.config.get("BACKUP_ROOT", "storage/backups")) / source_path

        upload_error = None
        if target is not None and source_path.exists() and backup.status != "scheduled":
            try:
                ok, message = validate_backup_target_connectivity(target)
                if not ok:
                    raise RuntimeError(message)
                upload_backup_to_target(backup)
            except Exception as exc:
                upload_error = str(exc)
                backup.status = "failed"

        pruned = apply_restore_points_retention(client)
        log_activity(
            "backups.create",
            "backup",
            f"Utworzono backup {backup.storage_path}",
            entity_id=backup.storage_path,
            client=client,
            actor=current_user,
            metadata={
                "storage_target_id": backup.storage_target_id,
                "external_location": backup.external_location,
                "retention_until": backup.retention_until.isoformat() if backup.retention_until else None,
                "pruned_by_restore_points": pruned,
                "upload_error": upload_error,
            },
            success=upload_error is None,
        )
        db.session.commit()
        if upload_error:
            flash(f"Backup utworzony, ale upload zewnetrzny nie powiodl sie: {upload_error}", "warning")
        else:
            flash("Backup został zaplanowany.", "success")
        return redirect(url_for("backups.admin_backups"))
    return render_template("backups/backup_form.html", form=form, title="Nowy backup")


@backups_bp.route("/admin/backups/targets/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_target_create():
    form = ExternalBackupTargetForm()
    if form.validate_on_submit():
        target = ExternalBackupTarget(
            name=(form.name.data or "").strip(),
            provider=form.provider.data,
            endpoint_url=(form.endpoint_url.data or "").strip() or None,
            bucket_name=(form.bucket_name.data or "").strip(),
            region=(form.region.data or "").strip() or None,
            access_key_env=(form.access_key_env.data or "").strip(),
            secret_key_env=(form.secret_key_env.data or "").strip(),
            is_active=False,
            created_by=current_user,
        )
        db.session.add(target)
        db.session.flush()

        is_ok, message = validate_backup_target_connectivity(target)
        if form.is_active.data and is_ok:
            target.is_active = True

        log_activity(
            "backups.target_create",
            "external_backup_target",
            f"Utworzono target backupu {target.name}",
            entity_id=target.id,
            actor=current_user,
            metadata={"provider": target.provider, "connectivity": message},
            success=is_ok,
        )
        db.session.commit()

        if is_ok:
            flash("Target backupu zapisany i polaczenie zweryfikowane.", "success")
        else:
            flash(f"Target zapisany, ale test polaczenia nie powiodl sie: {message}", "warning")
        return redirect(url_for("backups.admin_backups"))
    return render_template("backups/backup_target_form.html", form=form, title="Nowy target backupu")


@backups_bp.route("/admin/backups/targets/<int:target_id>/check", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_target_check(target_id: int):
    target = ExternalBackupTarget.query.get_or_404(target_id)
    is_ok, message = validate_backup_target_connectivity(target)
    log_activity(
        "backups.target_check",
        "external_backup_target",
        f"Test polaczenia targetu {target.name}",
        entity_id=target.id,
        actor=current_user,
        metadata={"message": message},
        success=is_ok,
    )
    db.session.commit()
    flash(message, "success" if is_ok else "warning")
    return redirect(url_for("backups.admin_backups"))


@backups_bp.route("/admin/backups/<int:backup_id>/verify", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_verify_backup(backup_id: int):
    backup = Backup.query.get_or_404(backup_id)
    run = verify_backup(backup, schedule_type="manual")
    db.session.commit()
    flash(
        f"Weryfikacja backupu #{backup.id}: {run.status} ({run.validation_message or '-'})",
        "success" if run.status == "success" else "warning",
    )
    return redirect(url_for("backups.admin_backups"))


@backups_bp.route("/admin/backups/verify/<string:schedule_type>", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_verify_schedule(schedule_type: str):
    mode = "weekly" if schedule_type == "weekly" else "daily"
    summary = run_verification_schedule(schedule_type=mode, limit=50)
    db.session.commit()
    flash(
        f"Weryfikacja ({mode}): processed={summary['processed']} success={summary['success']} failed={summary['failed']}",
        "info",
    )
    return redirect(url_for("backups.admin_backups"))


@backups_bp.route("/admin/backups/<int:backup_id>/restore", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_restore_request(backup_id: int):
    backup = Backup.query.get_or_404(backup_id)
    job = create_restore_job(backup=backup, requested_by=current_user)
    process_restore_job(job)
    log_activity(
        "backups.restore_admin",
        "backup_restore_job",
        f"Administrator uruchomil restore backupu #{backup.id}",
        entity_id=job.id,
        client=backup.client,
        actor=current_user,
        metadata={"backup_id": backup.id, "restore_type": job.restore_type, "status": job.status},
    )
    db.session.commit()
    flash(f"Restore backupu zostal uruchomiony (status: {job.status}).", "success")
    return redirect(url_for("backups.admin_backups"))


@backups_bp.route("/admin/backups/restores/<int:job_id>/process", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_restore_process(job_id: int):
    job = BackupRestoreJob.query.get_or_404(job_id)
    process_restore_job(job)
    log_activity(
        "backups.restore_process",
        "backup_restore_job",
        f"Przetworzono restore job #{job.id}",
        entity_id=job.id,
        client=job.client,
        actor=current_user,
        metadata={"backup_id": job.backup_id, "restore_type": job.restore_type, "status": job.status},
    )
    db.session.commit()
    flash(f"Job restore #{job.id} przetworzony (status: {job.status}).", "info")
    return redirect(url_for("backups.admin_backups"))


@backups_bp.route("/client/backups")
@login_required
@roles_required("client")
@active_account_required
def client_backups():
    client = current_client()
    restore_jobs = (
        BackupRestoreJob.query.filter_by(client_id=client.id)
        .order_by(BackupRestoreJob.created_at.desc())
        .limit(50)
        .all()
    )
    latest_runs = (
        BackupVerificationRun.query.join(Backup, BackupVerificationRun.backup_id == Backup.id)
        .filter(Backup.client_id == client.id)
        .order_by(BackupVerificationRun.created_at.desc())
        .all()
    )
    latest_by_backup: dict[int, BackupVerificationRun] = {}
    for run in latest_runs:
        latest_by_backup.setdefault(run.backup_id, run)
    return render_template(
        "backups/client_backups.html",
        backups=client.backups,
        restore_jobs=restore_jobs,
        latest_verification_by_backup=latest_by_backup,
    )


@backups_bp.route("/client/backups/<int:backup_id>/restore", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_restore_request(backup_id: int):
    client = current_client()
    backup = Backup.query.get_or_404(backup_id)
    if backup.client_id != client.id:
        abort(404)

    job = create_restore_job(backup=backup, requested_by=current_user)
    process_restore_job(job)
    log_activity(
        "backups.restore_client",
        "backup_restore_job",
        f"Klient uruchomil restore backupu #{backup.id}",
        entity_id=job.id,
        client=client,
        actor=current_user,
        metadata={"backup_id": backup.id, "restore_type": job.restore_type, "status": job.status},
    )
    db.session.commit()

    if job.status == "queued":
        flash("Restore zostal dodany do kolejki i wymaga wykonania przez administratora.", "warning")
    elif job.status == "completed":
        flash("Backup zostal przywrocony do katalogu restore.", "success")
    else:
        flash(f"Restore zakonczyl sie statusem: {job.status}.", "danger")
    return redirect(url_for("backups.client_backups"))
