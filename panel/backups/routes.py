from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from panel.extensions import db
from panel.forms.services import BackupForm
from panel.models import Backup, Client
from panel.services.audit import log_activity
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
    return render_template("backups/admin_backups.html", backups=Backup.query.order_by(Backup.created_at.desc()).all())


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


@backups_bp.route("/client/backups")
@login_required
@roles_required("client")
@active_account_required
def client_backups():
    client = current_client()
    return render_template("backups/client_backups.html", backups=client.backups)
