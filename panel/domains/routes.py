from __future__ import annotations

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from panel.extensions import db
from panel.forms.services import DomainForm, SubdomainForm
from panel.models import Client, Domain, Subdomain
from panel.services.approvals import action_requires_approval, create_approval_request
from panel.services.audit import log_activity
from panel.services.client_apache import ClientApacheServiceError, sync_client_apache_instance
from panel.services.domains import (
    DomainProvisioningError,
    managed_domain_public_root,
    managed_subdomain_public_root,
    provision_domain_tree,
    provision_subdomain_tree,
)
from panel.services.policy_engine import PolicyViolationError, enforce_policies
from panel.services.registrar import RegistrarError, register_domain_with_registrar, renew_domain_registration, sync_domain_registration
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


def _name_servers_from_text(value: str | None) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for line in (value or "").splitlines():
        normalized = line.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        items.append(normalized)
    return items


@domains_bp.route("/admin/domains")
@login_required
@roles_required("administrator")
def admin_domains():
    domains = (
        Domain.query.options(selectinload(Domain.registration), selectinload(Domain.client))
        .order_by(Domain.created_at.desc())
        .all()
    )
    return render_template("domains/admin_domains.html", domains=domains)


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
        register_now = bool(form.register_in_registrar.data)
        registrar_warning: str | None = None
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
            db.session.flush()
            if register_now:
                try:
                    register_domain_with_registrar(
                        domain,
                        years=form.registration_years.data or 1,
                        auto_renew=bool(form.auto_renew.data),
                        name_servers=_name_servers_from_text(form.name_servers.data),
                        actor=current_user,
                    )
                except RegistrarError as exc:
                    registrar_warning = str(exc)
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
        if register_now and registrar_warning:
            flash(f"Domena lokalna zostala utworzona, ale rejestracja registrar nie powiodla sie: {registrar_warning}", "warning")
        elif register_now:
            flash("Domena zostala zarejestrowana u providera registrar.", "info")
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
    if request.method == "GET" and domain.registration is not None:
        form.register_in_registrar.data = True
        form.auto_renew.data = bool(domain.registration.auto_renew)
        form.name_servers.data = "\n".join(domain.registration.name_servers_json or [])
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        domain_name = form.name.data.strip().lower()
        php_version = form.php_version.data.strip()
        register_now = bool(form.register_in_registrar.data)
        registrar_warning: str | None = None
        try:
            paths = provision_domain_tree(client, domain_name, php_version)
            domain.client_id = form.client_id.data
            domain.client_service_id = form.client_service_id.data or None
            domain.name = domain_name
            domain.document_root = paths["public"]
            domain.php_version = php_version
            domain.status = form.status.data
            domain.is_primary = form.is_primary.data
            if domain.registration is not None:
                domain.registration.auto_renew = bool(form.auto_renew.data)
                domain.registration.name_servers_json = _name_servers_from_text(form.name_servers.data)
            elif register_now:
                try:
                    register_domain_with_registrar(
                        domain,
                        years=form.registration_years.data or 1,
                        auto_renew=bool(form.auto_renew.data),
                        name_servers=_name_servers_from_text(form.name_servers.data),
                        actor=current_user,
                    )
                except RegistrarError as exc:
                    registrar_warning = str(exc)
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
        if register_now and registrar_warning:
            flash(f"Nie udalo sie zarejestrowac domeny w registrarze: {registrar_warning}", "warning")
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

    requires_approval = action_requires_approval("domains.delete")
    try:
        enforce_policies(
            event_type="domains.delete.request",
            context={
                "action": "domains.delete",
                "client_id": client.id if client else None,
                "domain_id": domain.id,
                "requires_approval": requires_approval,
                "approval_granted": False,
            },
            client=client,
            actor=current_user,
            target_type="domain",
            target_id=domain.id,
        )
    except PolicyViolationError as exc:
        log_activity(
            "policy.domains_delete_blocked",
            "domain",
            f"Policy zablokowala usuniecie domeny {name}",
            entity_id=domain.id,
            client=client,
            actor=current_user,
            success=False,
            metadata={"reason": str(exc), "action": "domains.delete"},
        )
        db.session.commit()
        flash(f"Usuniecie domeny zablokowane przez policy: {exc}", "danger")
        return redirect(url_for("domains.admin_domains"))

    if requires_approval:
        approval_request, created = create_approval_request(
            action_key="domains.delete",
            target_type="domain",
            target_id=domain.id,
            requested_by=current_user,
            reason=f"Usuniecie domeny {name}",
            client=client,
            metadata={"domain_name": name, "domain_id": domain.id},
        )
        db.session.commit()
        if created:
            flash(
                f"Wniosek o usuniecie domeny {name} zostal utworzony (#{approval_request.id}) i czeka na akceptacje.",
                "warning",
            )
        else:
            flash(
                f"Istnieje juz aktywny wniosek o usuniecie domeny {name} (#{approval_request.id}).",
                "info",
            )
        return redirect(url_for("domains.admin_domains"))

    db.session.delete(domain)
    log_activity("domains.delete", "domain", f"Usunieto domene {name}", entity_id=domain_id, client=client)
    db.session.commit()
    flash("Domena zostala usunieta.", "warning")
    _sync_client_apache(client, "domains.delete")
    return redirect(url_for("domains.admin_domains"))


@domains_bp.route("/admin/domains/<int:domain_id>/registrar/register", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_domain_registrar_register(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    years_raw = (request.form.get("years") or "1").strip()
    try:
        years = int(years_raw)
    except ValueError:
        years = 1
    auto_renew = request.form.get("auto_renew") != "0"

    try:
        register_domain_with_registrar(
            domain,
            years=years,
            auto_renew=auto_renew,
            name_servers=_name_servers_from_text(request.form.get("name_servers")),
            actor=current_user,
        )
        db.session.commit()
        flash(f"Domena {domain.name} zostala zarejestrowana w registrarze.", "success")
    except RegistrarError as exc:
        db.session.rollback()
        flash(f"Rejestracja domeny nie powiodla sie: {exc}", "danger")
    return redirect(url_for("domains.admin_domains"))


@domains_bp.route("/admin/domains/<int:domain_id>/registrar/renew", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_domain_registrar_renew(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    if domain.registration is None:
        flash("Domena nie ma aktywnej rejestracji registrar.", "warning")
        return redirect(url_for("domains.admin_domains"))

    years_raw = (request.form.get("years") or "1").strip()
    try:
        years = int(years_raw)
    except ValueError:
        years = 1
    try:
        renew_domain_registration(domain.registration, years=years, actor=current_user)
        db.session.commit()
        flash(f"Domena {domain.name} zostala odnowiona.", "success")
    except RegistrarError as exc:
        db.session.rollback()
        flash(f"Nie udalo sie odnowic domeny: {exc}", "danger")
    return redirect(url_for("domains.admin_domains"))


@domains_bp.route("/admin/domains/<int:domain_id>/registrar/sync", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_domain_registrar_sync(domain_id: int):
    domain = Domain.query.get_or_404(domain_id)
    if domain.registration is None:
        flash("Domena nie ma rejestracji registrar do synchronizacji.", "warning")
        return redirect(url_for("domains.admin_domains"))

    try:
        sync_domain_registration(domain.registration, actor=current_user)
        db.session.commit()
        flash(f"Synchronizacja registrar dla domeny {domain.name} zakonczona.", "success")
    except RegistrarError as exc:
        db.session.rollback()
        flash(f"Nie udalo sie zsynchronizowac domeny: {exc}", "danger")
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
