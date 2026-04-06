from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.forms.hosts import HostsEntryForm, RestoreHostsBackupForm
from panel.models import HostsFileBackup, HostsFileChange
from panel.services.audit import log_activity
from panel.services.hosts import HostsHelperError, run_hosts_helper
from panel.utils.decorators import roles_required


hosts_bp = Blueprint("hosts", __name__)


@hosts_bp.route("/admin/hosts", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def index():
    form = HostsEntryForm()
    restore_form = RestoreHostsBackupForm()
    payload = {"action": "list"}
    current_hosts = []
    try:
        current_hosts = run_hosts_helper(payload).get("entries", [])
    except HostsHelperError as exc:
        flash(str(exc), "danger")

    if form.validate_on_submit():
        action = request.form.get("action_name", "add")
        helper_payload = {
            "action": action,
            "ip_address": form.ip_address.data.strip(),
            "hostname": form.hostname.data.strip().lower(),
            "previous_value": form.previous_value.data.strip() if form.previous_value.data else None,
            "force_critical": bool(form.confirm_critical.data),
            "actor": current_user.username,
        }
        try:
            result = run_hosts_helper(helper_payload)
            backup = HostsFileBackup(
                backup_name=result["backup_name"],
                backup_path=result["backup_path"],
                checksum=result["checksum"],
                created_by=current_user,
                notes=result.get("message"),
            )
            db.session.add(backup)
            db.session.flush()
            change = HostsFileChange(
                backup=backup,
                user=current_user,
                action=action,
                ip_address=form.ip_address.data.strip(),
                hostname=form.hostname.data.strip().lower(),
                previous_value=form.previous_value.data.strip() if form.previous_value.data else None,
                new_value=form.ip_address.data.strip(),
                success=True,
                message=result.get("message", "Operacja wykonana"),
            )
            db.session.add(change)
            log_activity("hosts.change", "hosts_file", f"Zmiana pliku hosts: {action} {form.hostname.data}", entity_id=backup.id)
            db.session.commit()
            flash("Operacja na pliku hosts została wykonana.", "success")
            return redirect(url_for("hosts.index"))
        except HostsHelperError as exc:
            db.session.add(
                HostsFileChange(
                    user=current_user,
                    action=action,
                    ip_address=form.ip_address.data.strip(),
                    hostname=form.hostname.data.strip().lower(),
                    previous_value=form.previous_value.data.strip() if form.previous_value.data else None,
                    new_value=form.ip_address.data.strip(),
                    success=False,
                    message=str(exc),
                )
            )
            log_activity("hosts.change_failed", "hosts_file", f"Nieudana zmiana hosts: {action}", success=False)
            db.session.commit()
            flash(str(exc), "danger")

    backups = HostsFileBackup.query.order_by(HostsFileBackup.created_at.desc()).limit(20).all()
    changes = HostsFileChange.query.order_by(HostsFileChange.created_at.desc()).limit(20).all()
    return render_template(
        "hosts/index.html",
        form=form,
        restore_form=restore_form,
        current_hosts=current_hosts,
        backups=backups,
        changes=changes,
    )


@hosts_bp.route("/admin/hosts/restore", methods=["POST"])
@login_required
@roles_required("administrator")
def restore():
    form = RestoreHostsBackupForm()
    if form.validate_on_submit():
        try:
            result = run_hosts_helper(
                {
                    "action": "restore",
                    "backup_name": form.backup_name.data,
                    "actor": current_user.username,
                }
            )
            log_activity("hosts.restore", "hosts_file", f"Przywrócono backup {form.backup_name.data}")
            db.session.add(
                HostsFileChange(
                    user=current_user,
                    action="restore",
                    ip_address="-",
                    hostname=form.backup_name.data,
                    success=True,
                    message=result.get("message", "Przywrócono backup"),
                )
            )
            db.session.commit()
            flash("Backup pliku hosts został przywrócony.", "success")
        except HostsHelperError as exc:
            flash(str(exc), "danger")
    return redirect(url_for("hosts.index"))
