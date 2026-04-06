from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from panel.extensions import db
from panel.forms.services import FTPAccountForm
from panel.models import Client, FTPAccount
from panel.services.audit import log_activity
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, owned_or_404, service_choices


ftp_bp = Blueprint("ftp", __name__)


def _populate_form(form: FTPAccountForm):
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.client_service_id.choices = service_choices(selected_client_id)


@ftp_bp.route("/admin/ftp")
@login_required
@roles_required("administrator")
def admin_accounts():
    return render_template("ftp/admin_ftp.html", accounts=FTPAccount.query.order_by(FTPAccount.created_at.desc()).all())


@ftp_bp.route("/admin/ftp/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_create():
    form = FTPAccountForm()
    _populate_form(form)
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        account = FTPAccount(
            client=client,
            client_service_id=form.client_service_id.data or None,
            username=form.username.data,
            home_directory=form.home_directory.data,
            is_active=form.is_active.data,
        )
        if form.password.data:
            account.set_password(form.password.data)
        db.session.add(account)
        log_activity("ftp.create", "ftp_account", f"Utworzono konto FTP {account.username}", entity_id=account.username, client=client)
        db.session.commit()
        flash("Konto FTP zostało utworzone.", "success")
        return redirect(url_for("ftp.admin_accounts"))
    return render_template("ftp/ftp_form.html", form=form, title="Nowe konto FTP")


@ftp_bp.route("/client/ftp")
@login_required
@roles_required("client")
@active_account_required
def client_accounts():
    client = current_client()
    return render_template("ftp/client_ftp.html", accounts=client.ftp_accounts)


@ftp_bp.route("/client/ftp/<int:account_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_edit(account_id: int):
    account = owned_or_404(FTPAccount, account_id)
    form = FTPAccountForm(obj=account)
    form.client_id.choices = [(account.client_id, account.client.user.username)]
    form.client_service_id.choices = service_choices(account.client_id)
    if form.validate_on_submit():
        account.home_directory = form.home_directory.data
        account.is_active = form.is_active.data
        if form.password.data:
            account.set_password(form.password.data)
        log_activity("ftp.client_edit", "ftp_account", f"Klient zaktualizował konto FTP {account.username}", entity_id=account.id, client=account.client)
        db.session.commit()
        flash("Konto FTP zostało zaktualizowane.", "success")
        return redirect(url_for("ftp.client_accounts"))
    return render_template("ftp/ftp_form.html", form=form, title=f"Edycja {account.username}")
