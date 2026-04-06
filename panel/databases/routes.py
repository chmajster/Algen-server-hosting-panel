from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from panel.extensions import db
from panel.forms.services import DatabaseForm, DatabaseUserForm
from panel.models import Client, DatabaseUser, HostingDatabase
from panel.services.audit import log_activity
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, database_choices, owned_or_404, service_choices


databases_bp = Blueprint("databases", __name__)


def _populate_database_form(form: DatabaseForm):
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.client_service_id.choices = service_choices(selected_client_id)


def _populate_database_user_form(form: DatabaseUserForm, client_id: int | None = None):
    form.database_id.choices = database_choices(client_id)


@databases_bp.route("/admin/databases")
@login_required
@roles_required("administrator")
def admin_databases():
    databases = HostingDatabase.query.order_by(HostingDatabase.created_at.desc()).all()
    db_users = DatabaseUser.query.order_by(DatabaseUser.created_at.desc()).all()
    return render_template("databases/admin_databases.html", databases=databases, db_users=db_users)


@databases_bp.route("/admin/databases/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_database_create():
    form = DatabaseForm()
    _populate_database_form(form)
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        database = HostingDatabase(
            client=client,
            client_service_id=form.client_service_id.data or None,
            name=form.name.data,
            engine=form.engine.data,
            charset=form.charset.data,
            collation=form.collation.data,
            status=form.status.data,
        )
        db.session.add(database)
        log_activity("databases.create", "database", f"Utworzono bazę {database.name}", entity_id=database.name, client=client)
        db.session.commit()
        flash("Baza danych została utworzona.", "success")
        return redirect(url_for("databases.admin_databases"))
    return render_template("databases/database_form.html", form=form, title="Nowa baza danych")


@databases_bp.route("/admin/databases/<int:database_id>/users/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_database_user_create(database_id: int):
    database_obj = HostingDatabase.query.get_or_404(database_id)
    form = DatabaseUserForm()
    _populate_database_user_form(form, database_obj.client_id)
    form.database_id.data = database_obj.id
    if form.validate_on_submit():
        db_user = DatabaseUser(
            database=database_obj,
            username=form.username.data,
            host=form.host.data,
            status=form.status.data,
            privileges=["ALL"],
        )
        if form.password.data:
            db_user.set_password(form.password.data)
        db.session.add(db_user)
        log_activity("databases.user_create", "database_user", f"Utworzono użytkownika DB {db_user.username}", entity_id=db_user.username, client=database_obj.client)
        db.session.commit()
        flash("Użytkownik bazy został utworzony.", "success")
        return redirect(url_for("databases.admin_databases"))
    return render_template("databases/database_user_form.html", form=form, database=database_obj, title="Nowy użytkownik DB")


@databases_bp.route("/client/databases")
@login_required
@roles_required("client")
@active_account_required
def client_databases():
    client = current_client()
    return render_template("databases/client_databases.html", databases=client.databases)


@databases_bp.route("/client/databases/<int:database_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_database_edit(database_id: int):
    database_obj = owned_or_404(HostingDatabase, database_id)
    form = DatabaseForm(obj=database_obj)
    form.client_id.choices = [(database_obj.client_id, database_obj.client.user.username)]
    form.client_service_id.choices = service_choices(database_obj.client_id)
    if form.validate_on_submit():
        database_obj.charset = form.charset.data
        database_obj.collation = form.collation.data
        database_obj.status = form.status.data
        log_activity("databases.client_edit", "database", f"Klient zaktualizował bazę {database_obj.name}", entity_id=database_obj.id, client=database_obj.client)
        db.session.commit()
        flash("Baza została zaktualizowana.", "success")
        return redirect(url_for("databases.client_databases"))
    return render_template("databases/database_form.html", form=form, title=f"Edycja {database_obj.name}")
