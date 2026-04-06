from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError

from panel.extensions import db
from panel.forms.services import DomainForm, SubdomainForm
from panel.models import Client, Domain, Subdomain
from panel.services.audit import log_activity
from panel.services.client_apache import ClientApacheServiceError, sync_client_apache_instance
from panel.services.domains import (
    DomainProvisioningError,
    managed_domain_public_root,
    managed_subdomain_public_root,
    provision_domain_tree,
    provision_subdomain_tree,
)
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, owned_or_404, service_choices


domains_bp = Blueprint("domains", __name__)


def _populate_domain_form(form: DomainForm) -> None:
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.client_service_id.choices = service_choices(selected_client_id)


def _managed_domain_preview(client: Client | None, domain_name: str | None) -> str:
    if client is None or not domain_name:
        return ""
    return str(managed_domain_public_root(client, domain_name))


def _sync_client_apache(client: Client, reason: str) -> None:
    try:
        sync_client_apache_instance(client, reason=reason, actor=current_user if current_user.is_authenticated else None)
        db.session.commit()
    except ClientApacheServiceError as exc:
        db.session.rollback()
        current_app.logger.warning("Client Apache sync failed for client_id=%s: %s", client.id, exc)
        flash(f"Uwaga: nie udalo sie zsynchronizowac kontenera Apache klienta: {exc}", "warning")


@domains_bp.route("/admin/domains")
@login_required
@roles_required("administrator")
def admin_domains():
    return render_template("domains/admin_domains.html", domains=Domain.query.order_by(Domain.created_at.desc()).all())


@domains_bp.route("/admin/domains/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_domain_create():
    form = DomainForm()
    _populate_domain_form(form)
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        domain_name = form.name.data.strip().lower()
        php_version = form.php_version.data.strip()
        domain = Domain(
            client=client,
            client_service_id=form.client_service_id.data or None,
            name=domain_name,
            document_root="",
            php_version=php_version,
            status=form.status.data,
            is_primary=form.is_primary.data,
        )
        try:
            paths = provision_domain_tree(client, domain_name, php_version)
            domain.document_root = paths["public"]
            db.session.add(domain)
            log_activity(
                "domains.create",
                "domain",
                f"Utworzono domene {domain.name}",
                entity_id=domain.name,
                client=client,
                metadata=paths,
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Taka domena juz istnieje.", "danger")
            return render_template(
                "domains/admin_domain_form.html",
                form=form,
                title="Nowa domena",
                managed_root_preview=_managed_domain_preview(client, domain_name),
            )
        except (OSError, DomainProvisioningError) as exc:
            db.session.rollback()
            flash(f"Nie udalo sie przygotowac struktury katalogow domeny: {exc}", "danger")
            return render_template(
                "domains/admin_domain_form.html",
                form=form,
                title="Nowa domena",
                managed_root_preview=_managed_domain_preview(client, domain_name),
            )
        flash("Domena zostala utworzona wraz z katalogami public, private, subdomains, ssl i config.", "success")
        _sync_client_apache(client, "domains.create")
        return redirect(url_for("domains.admin_domains"))

    preview_client = Client.query.get(form.client_id.data) if form.client_id.data else None
    return render_template(
        "domains/admin_domain_form.html",
        form=form,
        title="Nowa domena",
        managed_root_preview=_managed_domain_preview(preview_client, form.name.data),
    )


@domains_bp.route("/admin/domains/<int:domain_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_domain_edit(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    form = DomainForm(obj=domain)
    form.client_id.data = domain.client_id
    _populate_domain_form(form)
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        domain_name = form.name.data.strip().lower()
        php_version = form.php_version.data.strip()
        try:
            paths = provision_domain_tree(client, domain_name, php_version)
            domain.client_id = form.client_id.data
            domain.client_service_id = form.client_service_id.data or None
            domain.name = domain_name
            domain.document_root = paths["public"]
            domain.php_version = php_version
            domain.status = form.status.data
            domain.is_primary = form.is_primary.data
            log_activity(
                "domains.edit",
                "domain",
                f"Zaktualizowano domene {domain.name}",
                entity_id=domain.id,
                client=client,
                metadata=paths,
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Nie mozna zapisac zmian, bo taka domena juz istnieje.", "danger")
            return render_template(
                "domains/admin_domain_form.html",
                form=form,
                title=f"Edycja {domain.name}",
                managed_root_preview=_managed_domain_preview(client, domain_name),
            )
        except (OSError, DomainProvisioningError) as exc:
            db.session.rollback()
            flash(f"Nie udalo sie odswiezyc struktury katalogow domeny: {exc}", "danger")
            return render_template(
                "domains/admin_domain_form.html",
                form=form,
                title=f"Edycja {domain.name}",
                managed_root_preview=_managed_domain_preview(client, domain_name),
            )
        flash("Domena zostala zaktualizowana.", "success")
        _sync_client_apache(client, "domains.edit")
        return redirect(url_for("domains.admin_domains"))

    return render_template(
        "domains/admin_domain_form.html",
        form=form,
        title=f"Edycja {domain.name}",
        managed_root_preview=_managed_domain_preview(domain.client, domain.name),
    )


@domains_bp.route("/admin/domains/<int:domain_id>/delete", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_domain_delete(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    client = domain.client
    name = domain.name
    db.session.delete(domain)
    log_activity("domains.delete", "domain", f"Usunieto domene {name}", entity_id=domain_id, client=client)
    db.session.commit()
    flash("Domena zostala usunieta.", "warning")
    _sync_client_apache(client, "domains.delete")
    return redirect(url_for("domains.admin_domains"))


@domains_bp.route("/admin/domains/<int:domain_id>/subdomains/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_subdomain_create(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    form = SubdomainForm()
    if form.validate_on_submit():
        subdomain_name = form.name.data.strip().lower()
        php_version = form.php_version.data.strip()
        subdomain = Subdomain(
            domain=domain,
            name=subdomain_name,
            document_root="",
            php_version=php_version,
            status=form.status.data,
        )
        try:
            paths = provision_subdomain_tree(domain, subdomain_name, php_version)
            subdomain.document_root = paths["public"]
            db.session.add(subdomain)
            log_activity(
                "domains.subdomain_create",
                "subdomain",
                f"Utworzono subdomene {subdomain.name}",
                entity_id=subdomain.name,
                client=domain.client,
                metadata=paths,
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("Taka subdomena juz istnieje dla tej domeny.", "danger")
            return render_template(
                "domains/subdomain_form.html",
                form=form,
                domain=domain,
                title="Nowa subdomena",
                managed_root_preview=str(managed_subdomain_public_root(domain, subdomain_name)),
            )
        except (OSError, DomainProvisioningError) as exc:
            db.session.rollback()
            flash(f"Nie udalo sie przygotowac katalogow subdomeny: {exc}", "danger")
            return render_template(
                "domains/subdomain_form.html",
                form=form,
                domain=domain,
                title="Nowa subdomena",
                managed_root_preview=str(managed_subdomain_public_root(domain, subdomain_name)),
            )
        flash("Subdomena zostala dodana wraz z wlasna struktura katalogow.", "success")
        _sync_client_apache(domain.client, "domains.subdomain_create")
        return redirect(url_for("domains.admin_domains"))

    return render_template(
        "domains/subdomain_form.html",
        form=form,
        domain=domain,
        title="Nowa subdomena",
        managed_root_preview=str(managed_subdomain_public_root(domain, form.name.data or "subdomain")),
    )


@domains_bp.route("/client/domains")
@login_required
@roles_required("client")
@active_account_required
def client_domains():
    client = current_client()
    return render_template("domains/client_domains.html", domains=client.domains)


@domains_bp.route("/client/domains/<int:domain_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_domain_edit(domain_id: int):
    domain = owned_or_404(Domain, domain_id)
    form = DomainForm(obj=domain)
    form.client_id.choices = [(domain.client_id, domain.client.user.username)]
    form.client_service_id.choices = service_choices(domain.client_id)
    if form.validate_on_submit():
        php_version = form.php_version.data.strip()
        try:
            paths = provision_domain_tree(domain.client, domain.name, php_version)
            domain.document_root = paths["public"]
            domain.php_version = php_version
            domain.status = form.status.data
            log_activity(
                "domains.client_edit",
                "domain",
                f"Klient zaktualizowal domene {domain.name}",
                entity_id=domain.id,
                client=domain.client,
                metadata=paths,
            )
            db.session.commit()
        except (OSError, DomainProvisioningError) as exc:
            db.session.rollback()
            flash(f"Nie udalo sie odswiezyc katalogow domeny: {exc}", "danger")
            return render_template(
                "domains/client_domain_form.html",
                form=form,
                domain=domain,
                title=f"Edycja {domain.name}",
                managed_root_preview=_managed_domain_preview(domain.client, domain.name),
            )
        flash("Domena zostala zaktualizowana.", "success")
        _sync_client_apache(domain.client, "domains.client_edit")
        return redirect(url_for("domains.client_domains"))

    return render_template(
        "domains/client_domain_form.html",
        form=form,
        domain=domain,
        title=f"Edycja {domain.name}",
        managed_root_preview=_managed_domain_preview(domain.client, domain.name),
    )
