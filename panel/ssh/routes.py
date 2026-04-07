from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.models import Client, ClientSSHKey
from panel.services.audit import log_activity
from panel.services.ssh_keys import SSHKeyError, create_client_ssh_key, delete_client_ssh_key, set_client_ssh_key_status
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import current_client


ssh_bp = Blueprint("ssh", __name__)


@ssh_bp.route("/client/ssh-keys")
@login_required
@roles_required("client")
@active_account_required
def client_keys():
    client = current_client()
    keys = ClientSSHKey.query.filter_by(client_id=client.id).order_by(ClientSSHKey.created_at.desc()).all()
    return render_template("ssh/client_keys.html", keys=keys)


@ssh_bp.route("/client/ssh-keys/add", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_key_add():
    client = current_client()
    label = (request.form.get("label") or "").strip()
    public_key = (request.form.get("public_key") or "").strip()

    if not public_key:
        flash("Wklej klucz publiczny SSH.", "warning")
        return redirect(url_for("ssh.client_keys"))

    try:
        row = create_client_ssh_key(
            client=client,
            public_key=public_key,
            label=label,
            created_by=current_user,
        )
        log_activity(
            "ssh_keys.create_client",
            "client_ssh_key",
            f"Dodano klucz SSH dla klienta {client.user.username if client.user else client.id}",
            entity_id=row.id,
            client=client,
            actor=current_user,
            metadata={"fingerprint": row.fingerprint_sha256, "key_type": row.key_type},
        )
        db.session.commit()
        flash("Klucz SSH zostal dodany i aktywowany.", "success")
    except SSHKeyError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssh.client_keys"))


@ssh_bp.route("/client/ssh-keys/<int:key_id>/toggle", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_key_toggle(key_id: int):
    client = current_client()
    row = ClientSSHKey.query.get_or_404(key_id)
    if row.client_id != client.id:
        abort(404)

    target_status = "disabled" if row.status == "active" else "active"
    try:
        set_client_ssh_key_status(key=row, status=target_status)
        log_activity(
            "ssh_keys.toggle_client",
            "client_ssh_key",
            f"Zmieniono status klucza SSH na {target_status}",
            entity_id=row.id,
            client=client,
            actor=current_user,
            metadata={"fingerprint": row.fingerprint_sha256, "status": target_status},
        )
        db.session.commit()
        flash("Status klucza SSH zostal zaktualizowany.", "info")
    except SSHKeyError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssh.client_keys"))


@ssh_bp.route("/client/ssh-keys/<int:key_id>/delete", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_key_delete(key_id: int):
    client = current_client()
    row = ClientSSHKey.query.get_or_404(key_id)
    if row.client_id != client.id:
        abort(404)

    try:
        fingerprint = row.fingerprint_sha256
        delete_client_ssh_key(key=row)
        log_activity(
            "ssh_keys.delete_client",
            "client_ssh_key",
            "Usunieto klucz SSH klienta",
            entity_id=key_id,
            client=client,
            actor=current_user,
            metadata={"fingerprint": fingerprint},
        )
        db.session.commit()
        flash("Klucz SSH zostal usuniety.", "warning")
    except SSHKeyError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssh.client_keys"))


@ssh_bp.route("/admin/ssh-keys")
@login_required
@roles_required("administrator")
def admin_keys():
    client_id = None
    client_id_raw = (request.args.get("client_id") or "").strip()
    if client_id_raw.isdigit():
        client_id = int(client_id_raw)

    query = ClientSSHKey.query.order_by(ClientSSHKey.created_at.desc())
    if client_id is not None:
        query = query.filter(ClientSSHKey.client_id == client_id)

    keys = query.limit(300).all()
    clients = Client.query.order_by(Client.created_at.desc()).limit(300).all()
    return render_template(
        "ssh/admin_keys.html",
        keys=keys,
        clients=clients,
        selected_client_id=client_id,
    )


@ssh_bp.route("/admin/ssh-keys/<int:key_id>/toggle", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_key_toggle(key_id: int):
    row = ClientSSHKey.query.get_or_404(key_id)
    target_status = "disabled" if row.status == "active" else "active"
    try:
        set_client_ssh_key_status(key=row, status=target_status)
        log_activity(
            "ssh_keys.toggle_admin",
            "client_ssh_key",
            f"Administrator zmienil status klucza SSH na {target_status}",
            entity_id=row.id,
            client=row.client,
            actor=current_user,
            metadata={"fingerprint": row.fingerprint_sha256, "status": target_status},
        )
        db.session.commit()
        flash("Status klucza SSH zostal zaktualizowany.", "info")
    except SSHKeyError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssh.admin_keys", client_id=row.client_id))


@ssh_bp.route("/admin/ssh-keys/<int:key_id>/delete", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_key_delete(key_id: int):
    row = ClientSSHKey.query.get_or_404(key_id)
    client_id = row.client_id
    try:
        fingerprint = row.fingerprint_sha256
        delete_client_ssh_key(key=row)
        log_activity(
            "ssh_keys.delete_admin",
            "client_ssh_key",
            "Administrator usunal klucz SSH klienta",
            entity_id=key_id,
            client=row.client,
            actor=current_user,
            metadata={"fingerprint": fingerprint},
        )
        db.session.commit()
        flash("Klucz SSH zostal usuniety.", "warning")
    except SSHKeyError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssh.admin_keys", client_id=client_id))
