from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from panel.extensions import db
from panel.forms.services import MailAliasForm, MailboxForm
from panel.models import Client, MailAlias, Mailbox
from panel.services.audit import log_activity
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, domain_choices, mailbox_choices, owned_or_404


mail_bp = Blueprint("mail", __name__)


def _populate_mailbox_form(form: MailboxForm):
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.domain_id.choices = domain_choices(selected_client_id)


def _populate_alias_form(form: MailAliasForm, client_id: int | None = None):
    form.mailbox_id.choices = mailbox_choices(client_id)


@mail_bp.route("/admin/mail")
@login_required
@roles_required("administrator")
def admin_mail():
    mailboxes = Mailbox.query.order_by(Mailbox.created_at.desc()).all()
    aliases = MailAlias.query.order_by(MailAlias.created_at.desc()).all()
    return render_template("mail/admin_mail.html", mailboxes=mailboxes, aliases=aliases)


@mail_bp.route("/admin/mail/mailboxes/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_mailbox_create():
    form = MailboxForm()
    _populate_mailbox_form(form)
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        mailbox = Mailbox(
            client=client,
            domain_id=form.domain_id.data,
            email=form.email.data.lower(),
            quota_mb=form.quota_mb.data,
            status=form.status.data,
        )
        if form.password.data:
            mailbox.set_password(form.password.data)
        db.session.add(mailbox)
        log_activity("mail.mailbox_create", "mailbox", f"Utworzono skrzynkę {mailbox.email}", entity_id=mailbox.email, client=client)
        db.session.commit()
        flash("Skrzynka została utworzona.", "success")
        return redirect(url_for("mail.admin_mail"))
    return render_template("mail/mailbox_form.html", form=form, title="Nowa skrzynka")


@mail_bp.route("/admin/mail/aliases/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_alias_create():
    form = MailAliasForm()
    _populate_alias_form(form)
    if form.validate_on_submit():
        mailbox = Mailbox.query.get_or_404(form.mailbox_id.data)
        alias = MailAlias(
            mailbox=mailbox,
            source=form.source.data.lower(),
            destination=form.destination.data.lower(),
            alias_type=form.alias_type.data,
        )
        db.session.add(alias)
        log_activity("mail.alias_create", "mail_alias", f"Utworzono alias {alias.source}", entity_id=alias.source, client=mailbox.client)
        db.session.commit()
        flash("Alias został utworzony.", "success")
        return redirect(url_for("mail.admin_mail"))
    return render_template("mail/alias_form.html", form=form, title="Nowy alias")


@mail_bp.route("/client/mail")
@login_required
@roles_required("client")
@active_account_required
def client_mail():
    client = current_client()
    return render_template("mail/client_mail.html", client=client)


@mail_bp.route("/client/mail/mailboxes/<int:mailbox_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_mailbox_edit(mailbox_id: int):
    mailbox = owned_or_404(Mailbox, mailbox_id)
    form = MailboxForm(obj=mailbox)
    form.client_id.choices = [(mailbox.client_id, mailbox.client.user.username)]
    form.domain_id.choices = domain_choices(mailbox.client_id)
    if form.validate_on_submit():
        mailbox.quota_mb = form.quota_mb.data
        mailbox.status = form.status.data
        if form.password.data:
            mailbox.set_password(form.password.data)
        log_activity("mail.client_mailbox_edit", "mailbox", f"Klient zaktualizował skrzynkę {mailbox.email}", entity_id=mailbox.id, client=mailbox.client)
        db.session.commit()
        flash("Skrzynka została zaktualizowana.", "success")
        return redirect(url_for("mail.client_mail"))
    return render_template("mail/mailbox_form.html", form=form, title=f"Edycja {mailbox.email}")
