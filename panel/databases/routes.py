from __future__ import annotations

import re

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required

from panel.extensions import db
from panel.forms.services import DatabaseForm, DatabaseUserForm
from panel.models import Client, DatabaseUser, HostingDatabase
from panel.services.audit import log_activity
from panel.services.resource_limits import hard_limit_block_reason
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, database_choices, owned_or_404, service_choices


databases_bp = Blueprint("databases", __name__)


DB_PRIVILEGE_CHOICES = [
    ("ALL", "ALL"),
    ("SELECT", "SELECT"),
    ("INSERT", "INSERT"),
    ("UPDATE", "UPDATE"),
    ("DELETE", "DELETE"),
    ("CREATE", "CREATE"),
    ("DROP", "DROP"),
    ("ALTER", "ALTER"),
    ("INDEX", "INDEX"),
    ("REFERENCES", "REFERENCES"),
    ("LOCK TABLES", "LOCK TABLES"),
    ("CREATE VIEW", "CREATE VIEW"),
    ("SHOW VIEW", "SHOW VIEW"),
    ("TRIGGER", "TRIGGER"),
]
DB_USER_SUFFIX_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def _normalize_privileges(values: list[str] | None) -> list[str]:
    allowed = {code for code, _ in DB_PRIVILEGE_CHOICES}
    selected: list[str] = []
    for value in values or []:
        if value in allowed and value not in selected:
            selected.append(value)
    if not selected:
        return ["ALL"]
    if "ALL" in selected:
        return ["ALL"]
    return selected


def _client_db_username_prefix(client: Client) -> str:
    return f"{client.user.username}_"


def _build_client_db_username(client: Client, raw_username: str) -> tuple[str | None, str | None]:
    prefix = _client_db_username_prefix(client)
    suffix = (raw_username or "").strip()
    if suffix.lower().startswith(prefix.lower()):
        suffix = suffix[len(prefix) :]
    if not suffix:
        return None, f"Podaj login DB bez prefiksu '{prefix}'."
    if DB_USER_SUFFIX_PATTERN.fullmatch(suffix) is None:
        return None, "Login DB moze zawierac tylko litery, cyfry i znak underscore (_)."
    username = f"{prefix}{suffix}"
    if len(username) > 120:
        return None, "Pelna nazwa uzytkownika DB jest zbyt dluga."
    return username, None


def _owned_database_user_or_404(db_user_id: int) -> DatabaseUser:
    client = current_client()
    db_user = (
        DatabaseUser.query.join(HostingDatabase)
        .filter(DatabaseUser.id == db_user_id, HostingDatabase.client_id == client.id)
        .first()
    )
    if db_user is None:
        abort(404)
    return db_user


def _populate_database_form(form: DatabaseForm):
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.client_service_id.choices = service_choices(selected_client_id)


def _populate_database_user_form(form: DatabaseUserForm, client_id: int | None = None):
    form.database_id.choices = database_choices(client_id)
    form.privileges.choices = DB_PRIVILEGE_CHOICES
    if not form.privileges.data:
        form.privileges.data = ["ALL"]


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
        limit_reason = hard_limit_block_reason(client, "database_count", upcoming_delta=1)
        if limit_reason is not None:
            flash(limit_reason, "danger")
            return render_template("databases/database_form.html", form=form, title="Nowa baza danych")
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
        if not form.password.data:
            flash("Haslo jest wymagane przy tworzeniu uzytkownika DB.", "danger")
            return render_template("databases/database_user_form.html", form=form, database=database_obj, title="Nowy użytkownik DB")
        db_user = DatabaseUser(
            database=database_obj,
            username=form.username.data,
            host=form.host.data,
            status=form.status.data,
            privileges=_normalize_privileges(form.privileges.data),
        )
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
    db_users = (
        DatabaseUser.query.join(HostingDatabase)
        .filter(HostingDatabase.client_id == client.id)
        .order_by(DatabaseUser.created_at.desc())
        .all()
    )
    return render_template("databases/client_databases.html", databases=client.databases, db_users=db_users)


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


@databases_bp.route("/client/databases/<int:database_id>/users/new", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_database_user_create(database_id: int):
    database_obj = owned_or_404(HostingDatabase, database_id)
    username_prefix = _client_db_username_prefix(database_obj.client)
    form = DatabaseUserForm()
    _populate_database_user_form(form, database_obj.client_id)
    form.database_id.choices = [(database_obj.id, database_obj.name)]
    form.database_id.data = database_obj.id

    if form.validate_on_submit():
        if not form.password.data:
            flash("Haslo jest wymagane przy tworzeniu uzytkownika DB.", "danger")
            return render_template(
                "databases/database_user_form.html",
                form=form,
                database=database_obj,
                title="Nowy użytkownik DB",
                username_prefix=username_prefix,
                show_database_field=False,
            )
        username, username_error = _build_client_db_username(database_obj.client, form.username.data)
        if username_error is not None:
            flash(username_error, "danger")
            return render_template(
                "databases/database_user_form.html",
                form=form,
                database=database_obj,
                title="Nowy użytkownik DB",
                username_prefix=username_prefix,
                show_database_field=False,
            )
        if DatabaseUser.query.filter_by(username=username).first() is not None:
            flash("Taki uzytkownik DB juz istnieje.", "danger")
            return render_template(
                "databases/database_user_form.html",
                form=form,
                database=database_obj,
                title="Nowy użytkownik DB",
                username_prefix=username_prefix,
                show_database_field=False,
            )

        db_user = DatabaseUser(
            database=database_obj,
            username=username,
            host=form.host.data,
            status=form.status.data,
            privileges=_normalize_privileges(form.privileges.data),
        )
        db_user.set_password(form.password.data)
        db.session.add(db_user)
        log_activity(
            "databases.client_user_create",
            "database_user",
            f"Klient utworzyl uzytkownika DB {db_user.username}",
            entity_id=db_user.username,
            client=database_obj.client,
        )
        db.session.commit()
        flash("Uzytkownik bazy zostal utworzony.", "success")
        return redirect(url_for("databases.client_databases"))

    return render_template(
        "databases/database_user_form.html",
        form=form,
        database=database_obj,
        title="Nowy użytkownik DB",
        username_prefix=username_prefix,
        show_database_field=False,
    )


@databases_bp.route("/client/databases/users/<int:db_user_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_database_user_edit(db_user_id: int):
    db_user = _owned_database_user_or_404(db_user_id)
    database_obj = db_user.database
    username_prefix = _client_db_username_prefix(database_obj.client)
    form = DatabaseUserForm(obj=db_user)
    _populate_database_user_form(form, database_obj.client_id)
    form.database_id.choices = [(database_obj.id, database_obj.name)]

    if request.method == "GET":
        form.database_id.data = database_obj.id
        if db_user.username.lower().startswith(username_prefix.lower()):
            form.username.data = db_user.username[len(username_prefix) :]
        else:
            form.username.data = db_user.username
        form.privileges.data = db_user.privileges or ["ALL"]

    if form.validate_on_submit():
        if form.database_id.data != database_obj.id:
            abort(400)

        username, username_error = _build_client_db_username(database_obj.client, form.username.data)
        if username_error is not None:
            flash(username_error, "danger")
            return render_template(
                "databases/database_user_form.html",
                form=form,
                database=database_obj,
                title=f"Edycja użytkownika DB {db_user.username}",
                username_prefix=username_prefix,
                show_database_field=False,
            )

        duplicate = DatabaseUser.query.filter(DatabaseUser.username == username, DatabaseUser.id != db_user.id).first()
        if duplicate is not None:
            flash("Taki uzytkownik DB juz istnieje.", "danger")
            return render_template(
                "databases/database_user_form.html",
                form=form,
                database=database_obj,
                title=f"Edycja użytkownika DB {db_user.username}",
                username_prefix=username_prefix,
                show_database_field=False,
            )

        db_user.username = username
        db_user.host = form.host.data
        db_user.status = form.status.data
        db_user.privileges = _normalize_privileges(form.privileges.data)
        if form.password.data:
            db_user.set_password(form.password.data)

        log_activity(
            "databases.client_user_edit",
            "database_user",
            f"Klient zaktualizowal uzytkownika DB {db_user.username}",
            entity_id=db_user.id,
            client=database_obj.client,
        )
        db.session.commit()
        flash("Uzytkownik bazy zostal zaktualizowany.", "success")
        return redirect(url_for("databases.client_databases"))

    return render_template(
        "databases/database_user_form.html",
        form=form,
        database=database_obj,
        title=f"Edycja użytkownika DB {db_user.username}",
        username_prefix=username_prefix,
        show_database_field=False,
    )
