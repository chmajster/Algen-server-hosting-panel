from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.forms.services import BackupForm
from panel.models import Backup, BackupRestoreJob, Client
from panel.services.audit import log_activity
from panel.services.backup_restore import create_restore_job, process_restore_job
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, database_choices, domain_choices


backups_bp = Blueprint("backups", __name__)


def _populate_form(form: BackupForm):
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.domain_id.choices = [(0, "Brak")] + domain_choices(selected_client_id)
    form.database_id.choices = [(0, "Brak")] + database_choices(selected_client_id)


@backups_bp.route("/admin/backups")
@login_required
@roles_required("administrator")
def admin_backups():
    backups = Backup.query.order_by(Backup.created_at.desc()).all()
    restore_jobs = BackupRestoreJob.query.order_by(BackupRestoreJob.created_at.desc()).limit(100).all()
    return render_template("backups/admin_backups.html", backups=backups, restore_jobs=restore_jobs)


@backups_bp.route("/admin/backups/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_create():
    form = BackupForm()
    _populate_form(form)
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        backup = Backup(
            client=client,
            domain_id=form.domain_id.data or None,
            database_id=form.database_id.data or None,
            backup_type=form.backup_type.data,
            status="scheduled" if form.scheduled_for.data else "queued",
            storage_path=form.storage_path.data,
            scheduled_for=form.scheduled_for.data,
        )
        db.session.add(backup)
        log_activity("backups.create", "backup", f"Utworzono backup {backup.storage_path}", entity_id=backup.storage_path, client=client)
        db.session.commit()
        flash("Backup został zaplanowany.", "success")
        return redirect(url_for("backups.admin_backups"))
    return render_template("backups/backup_form.html", form=form, title="Nowy backup")


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
    return render_template("backups/client_backups.html", backups=client.backups, restore_jobs=restore_jobs)


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
