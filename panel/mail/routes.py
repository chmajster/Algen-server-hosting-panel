from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy.exc import IntegrityError

from panel.extensions import db
from panel.forms.services import MailAliasForm, MailboxForm
from panel.models import Client, Domain, MailAlias, Mailbox
from panel.services.audit import log_activity
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, domain_choices, mailbox_choices, owned_or_404


mail_bp = Blueprint("mail", __name__)


def _domain_belongs_to_client(client_id: int, domain_id: int) -> bool:
    return Domain.query.filter_by(id=domain_id, client_id=client_id).first() is not None


def _populate_mailbox_form(form: MailboxForm, client_id: int | None = None, *, locked_client: bool = False) -> None:
    if locked_client and client_id is not None:
        client = Client.query.get_or_404(client_id)
        form.client_id.choices = [(client.id, client.user.username)]
        form.client_id.data = client.id
    else:
        form.client_id.choices = client_choices()

    selected_client_id = client_id or form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.domain_id.choices = domain_choices(selected_client_id)


def _populate_alias_form(form: MailAliasForm, client_id: int | None = None) -> None:
    form.mailbox_id.choices = mailbox_choices(client_id)


def _save_mailbox(form: MailboxForm, mailbox: Mailbox, *, client: Client, is_create: bool) -> bool:
    if is_create and not form.password.data:
        flash("Haslo jest wymagane przy tworzeniu skrzynki.", "danger")
        return False
    if not _domain_belongs_to_client(client.id, form.domain_id.data):
        flash("Wybrana domena nie nalezy do wskazanego klienta.", "danger")
        return False

    mailbox.client = client
    mailbox.domain_id = form.domain_id.data
    mailbox.email = form.email.data.lower()
    mailbox.quota_mb = form.quota_mb.data
    mailbox.status = form.status.data
    if form.password.data:
        mailbox.set_password(form.password.data)
    return True


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
        mailbox = Mailbox(client=client, password_hash="")
        if _save_mailbox(form, mailbox, client=client, is_create=True):
            db.session.add(mailbox)
            try:
                log_activity("mail.mailbox_create", "mailbox", f"Utworzono skrzynke {mailbox.email}", entity_id=mailbox.email, client=client)
                db.session.commit()
                flash("Skrzynka zostala utworzona.", "success")
                return redirect(url_for("mail.admin_mail"))
            except IntegrityError:
                db.session.rollback()
                flash("Skrzynka o takim adresie juz istnieje.", "danger")
    return render_template("mail/mailbox_form.html", form=form, title="Nowa skrzynka", locked_client=False)


@mail_bp.route("/admin/mail/mailboxes/<int:mailbox_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_mailbox_edit(mailbox_id: int):
    mailbox = Mailbox.query.get_or_404(mailbox_id)
    form = MailboxForm(obj=mailbox)
    selected_client_id = form.client_id.data if request.method == "POST" else mailbox.client_id
    _populate_mailbox_form(form, selected_client_id)
    if request.method == "GET":
        form.client_id.data = mailbox.client_id
        form.domain_id.data = mailbox.domain_id
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        if _save_mailbox(form, mailbox, client=client, is_create=False):
            try:
                log_activity("mail.mailbox_edit", "mailbox", f"Zaktualizowano skrzynke {mailbox.email}", entity_id=mailbox.id, client=client)
                db.session.commit()
                flash("Skrzynka zostala zaktualizowana.", "success")
                return redirect(url_for("mail.admin_mail"))
            except IntegrityError:
                db.session.rollback()
                flash("Skrzynka o takim adresie juz istnieje.", "danger")
    return render_template("mail/mailbox_form.html", form=form, title=f"Edycja {mailbox.email}", locked_client=False)


@mail_bp.route("/admin/mail/mailboxes/<int:mailbox_id>/delete", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_mailbox_delete(mailbox_id: int):
    mailbox = Mailbox.query.get_or_404(mailbox_id)
    email = mailbox.email
    client = mailbox.client
    db.session.delete(mailbox)
    log_activity("mail.mailbox_delete", "mailbox", f"Usunieto skrzynke {email}", entity_id=mailbox_id, client=client)
    db.session.commit()
    flash("Skrzynka zostala usunieta.", "warning")
    return redirect(url_for("mail.admin_mail"))


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
        try:
            log_activity("mail.alias_create", "mail_alias", f"Utworzono alias {alias.source}", entity_id=alias.source, client=mailbox.client)
            db.session.commit()
            flash("Alias zostal utworzony.", "success")
            return redirect(url_for("mail.admin_mail"))
        except IntegrityError:
            db.session.rollback()
            flash("Alias o takiej nazwie juz istnieje.", "danger")
    return render_template("mail/alias_form.html", form=form, title="Nowy alias")


@mail_bp.route("/admin/mail/aliases/<int:alias_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_alias_edit(alias_id: int):
    alias = MailAlias.query.get_or_404(alias_id)
    form = MailAliasForm(obj=alias)
    _populate_alias_form(form)
    if request.method == "GET":
        form.mailbox_id.data = alias.mailbox_id
    if form.validate_on_submit():
        mailbox = Mailbox.query.get_or_404(form.mailbox_id.data)
        alias.mailbox = mailbox
        alias.source = form.source.data.lower()
        alias.destination = form.destination.data.lower()
        alias.alias_type = form.alias_type.data
        try:
            log_activity("mail.alias_edit", "mail_alias", f"Zaktualizowano alias {alias.source}", entity_id=alias.id, client=mailbox.client)
            db.session.commit()
            flash("Alias zostal zaktualizowany.", "success")
            return redirect(url_for("mail.admin_mail"))
        except IntegrityError:
            db.session.rollback()
            flash("Alias o takiej nazwie juz istnieje.", "danger")
    return render_template("mail/alias_form.html", form=form, title=f"Edycja aliasu {alias.source}")


@mail_bp.route("/admin/mail/aliases/<int:alias_id>/delete", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_alias_delete(alias_id: int):
    alias = MailAlias.query.get_or_404(alias_id)
    source = alias.source
    client = alias.mailbox.client
    db.session.delete(alias)
    log_activity("mail.alias_delete", "mail_alias", f"Usunieto alias {source}", entity_id=alias_id, client=client)
    db.session.commit()
    flash("Alias zostal usuniety.", "warning")
    return redirect(url_for("mail.admin_mail"))


@mail_bp.route("/client/mail")
@login_required
@roles_required("client")
@active_account_required
def client_mail():
    client = current_client()
    aliases = MailAlias.query.join(Mailbox).filter(Mailbox.client_id == client.id).order_by(MailAlias.created_at.desc()).all()
    return render_template("mail/client_mail.html", client=client, aliases=aliases)


@mail_bp.route("/client/mail/mailboxes/new", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_mailbox_create():
    client = current_client()
    form = MailboxForm()
    _populate_mailbox_form(form, client.id, locked_client=True)
    if form.validate_on_submit():
        mailbox = Mailbox(client=client, password_hash="")
        if _save_mailbox(form, mailbox, client=client, is_create=True):
            db.session.add(mailbox)
            try:
                log_activity("mail.client_mailbox_create", "mailbox", f"Klient utworzyl skrzynke {mailbox.email}", entity_id=mailbox.email, client=client)
                db.session.commit()
                flash("Skrzynka zostala utworzona.", "success")
                return redirect(url_for("mail.client_mail"))
            except IntegrityError:
                db.session.rollback()
                flash("Skrzynka o takim adresie juz istnieje.", "danger")
    return render_template("mail/mailbox_form.html", form=form, title="Nowa skrzynka", locked_client=True)


@mail_bp.route("/client/mail/mailboxes/<int:mailbox_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_mailbox_edit(mailbox_id: int):
    mailbox = owned_or_404(Mailbox, mailbox_id)
    form = MailboxForm(obj=mailbox)
    _populate_mailbox_form(form, mailbox.client_id, locked_client=True)
    if request.method == "GET":
        form.client_id.data = mailbox.client_id
        form.domain_id.data = mailbox.domain_id
    if form.validate_on_submit():
        if _save_mailbox(form, mailbox, client=mailbox.client, is_create=False):
            try:
                log_activity("mail.client_mailbox_edit", "mailbox", f"Klient zaktualizowal skrzynke {mailbox.email}", entity_id=mailbox.id, client=mailbox.client)
                db.session.commit()
                flash("Skrzynka zostala zaktualizowana.", "success")
                return redirect(url_for("mail.client_mail"))
            except IntegrityError:
                db.session.rollback()
                flash("Skrzynka o takim adresie juz istnieje.", "danger")
    return render_template("mail/mailbox_form.html", form=form, title=f"Edycja {mailbox.email}", locked_client=True)


@mail_bp.route("/client/mail/mailboxes/<int:mailbox_id>/delete", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_mailbox_delete(mailbox_id: int):
    mailbox = owned_or_404(Mailbox, mailbox_id)
    email = mailbox.email
    client = mailbox.client
    db.session.delete(mailbox)
    log_activity("mail.client_mailbox_delete", "mailbox", f"Klient usunal skrzynke {email}", entity_id=mailbox_id, client=client)
    db.session.commit()
    flash("Skrzynka zostala usunieta.", "warning")
    return redirect(url_for("mail.client_mail"))


@mail_bp.route("/client/mail/aliases/new", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_alias_create():
    client = current_client()
    form = MailAliasForm()
    _populate_alias_form(form, client.id)
    if form.validate_on_submit():
        mailbox = owned_or_404(Mailbox, form.mailbox_id.data)
        alias = MailAlias(
            mailbox=mailbox,
            source=form.source.data.lower(),
            destination=form.destination.data.lower(),
            alias_type=form.alias_type.data,
        )
        db.session.add(alias)
        try:
            log_activity("mail.client_alias_create", "mail_alias", f"Klient utworzyl alias {alias.source}", entity_id=alias.source, client=client)
            db.session.commit()
            flash("Alias zostal utworzony.", "success")
            return redirect(url_for("mail.client_mail"))
        except IntegrityError:
            db.session.rollback()
            flash("Alias o takiej nazwie juz istnieje.", "danger")
    return render_template("mail/alias_form.html", form=form, title="Nowy alias")


@mail_bp.route("/client/mail/aliases/<int:alias_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_alias_edit(alias_id: int):
    alias = owned_or_404(MailAlias, alias_id)
    client = current_client()
    form = MailAliasForm(obj=alias)
    _populate_alias_form(form, client.id)
    if request.method == "GET":
        form.mailbox_id.data = alias.mailbox_id
    if form.validate_on_submit():
        mailbox = owned_or_404(Mailbox, form.mailbox_id.data)
        alias.mailbox = mailbox
        alias.source = form.source.data.lower()
        alias.destination = form.destination.data.lower()
        alias.alias_type = form.alias_type.data
        try:
            log_activity("mail.client_alias_edit", "mail_alias", f"Klient zaktualizowal alias {alias.source}", entity_id=alias.id, client=client)
            db.session.commit()
            flash("Alias zostal zaktualizowany.", "success")
            return redirect(url_for("mail.client_mail"))
        except IntegrityError:
            db.session.rollback()
            flash("Alias o takiej nazwie juz istnieje.", "danger")
    return render_template("mail/alias_form.html", form=form, title=f"Edycja aliasu {alias.source}")


@mail_bp.route("/client/mail/aliases/<int:alias_id>/delete", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_alias_delete(alias_id: int):
    alias = owned_or_404(MailAlias, alias_id)
    source = alias.source
    client = alias.mailbox.client
    db.session.delete(alias)
    log_activity("mail.client_alias_delete", "mail_alias", f"Klient usunal alias {source}", entity_id=alias_id, client=client)
    db.session.commit()
    flash("Alias zostal usuniety.", "warning")
    return redirect(url_for("mail.client_mail"))
