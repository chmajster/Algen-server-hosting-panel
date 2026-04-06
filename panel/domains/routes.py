from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from panel.extensions import db
from panel.forms.services import DomainForm, SubdomainForm
from panel.models import Client, Domain, Subdomain
from panel.services.audit import log_activity
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, owned_or_404, service_choices


domains_bp = Blueprint("domains", __name__)


def _populate_domain_form(form: DomainForm):
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.client_service_id.choices = service_choices(selected_client_id)


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
        domain = Domain(
            client=client,
            client_service_id=form.client_service_id.data or None,
            name=form.name.data.lower(),
            document_root=form.document_root.data,
            php_version=form.php_version.data,
            status=form.status.data,
            is_primary=form.is_primary.data,
        )
        db.session.add(domain)
        log_activity("domains.create", "domain", f"Utworzono domenę {domain.name}", entity_id=domain.name, client=client)
        db.session.commit()
        flash("Domena została utworzona.", "success")
        return redirect(url_for("domains.admin_domains"))
    return render_template("domains/admin_domain_form.html", form=form, title="Nowa domena")


@domains_bp.route("/admin/domains/<int:domain_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_domain_edit(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    form = DomainForm(obj=domain)
    form.client_id.data = domain.client_id
    _populate_domain_form(form)
    if form.validate_on_submit():
        domain.client_id = form.client_id.data
        domain.client_service_id = form.client_service_id.data or None
        domain.name = form.name.data.lower()
        domain.document_root = form.document_root.data
        domain.php_version = form.php_version.data
        domain.status = form.status.data
        domain.is_primary = form.is_primary.data
        log_activity("domains.edit", "domain", f"Zaktualizowano domenę {domain.name}", entity_id=domain.id, client=domain.client)
        db.session.commit()
        flash("Domena została zaktualizowana.", "success")
        return redirect(url_for("domains.admin_domains"))
    return render_template("domains/admin_domain_form.html", form=form, title=f"Edycja {domain.name}")


@domains_bp.route("/admin/domains/<int:domain_id>/delete", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_domain_delete(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    name = domain.name
    db.session.delete(domain)
    log_activity("domains.delete", "domain", f"Usunięto domenę {name}", entity_id=domain_id, client=domain.client)
    db.session.commit()
    flash("Domena została usunięta.", "warning")
    return redirect(url_for("domains.admin_domains"))


@domains_bp.route("/admin/domains/<int:domain_id>/subdomains/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_subdomain_create(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    form = SubdomainForm()
    if form.validate_on_submit():
        subdomain = Subdomain(
            domain=domain,
            name=form.name.data.lower(),
            document_root=form.document_root.data,
            php_version=form.php_version.data,
            status=form.status.data,
        )
        db.session.add(subdomain)
        log_activity("domains.subdomain_create", "subdomain", f"Utworzono subdomenę {subdomain.name}", entity_id=subdomain.name, client=domain.client)
        db.session.commit()
        flash("Subdomena została dodana.", "success")
        return redirect(url_for("domains.admin_domains"))
    return render_template("domains/subdomain_form.html", form=form, domain=domain, title="Nowa subdomena")


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
        domain.document_root = form.document_root.data
        domain.php_version = form.php_version.data
        domain.status = form.status.data
        log_activity("domains.client_edit", "domain", f"Klient zaktualizował domenę {domain.name}", entity_id=domain.id, client=domain.client)
        db.session.commit()
        flash("Domena została zaktualizowana.", "success")
        return redirect(url_for("domains.client_domains"))
    return render_template("domains/client_domain_form.html", form=form, domain=domain, title=f"Edycja {domain.name}")
