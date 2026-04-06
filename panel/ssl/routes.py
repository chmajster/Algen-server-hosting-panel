from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from panel.extensions import db
from panel.forms.services import SSLCertificateForm
from panel.models import SSLCertificate
from panel.services.ssl import (
    SSLServiceError,
    bind_certificate_target,
    issue_certificate,
    renew_certificate,
    resolve_ssl_target,
)
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import current_client, owned_or_404, ssl_target_choices


ssl_bp = Blueprint("ssl", __name__)


def _form_target_ref(cert: SSLCertificate) -> str | None:
    if cert.domain_id:
        return f"domain:{cert.domain_id}"
    if cert.subdomain_id:
        return f"subdomain:{cert.subdomain_id}"
    return None


def _populate_form(form: SSLCertificateForm, client_id: int | None = None, current_target_ref: str | None = None) -> None:
    choices = ssl_target_choices(client_id)
    if current_target_ref and not any(value == current_target_ref for value, _label in choices):
        choices.insert(0, (current_target_ref, f"Aktualne powiazanie: {current_target_ref}"))
    form.target_ref.choices = choices


def _ensure_targets_available(form: SSLCertificateForm) -> bool:
    if form.target_ref.choices:
        return True
    flash("Brak dostepnych domen lub subdomen dla certyfikatow SSL.", "warning")
    return False


def _cert_form_to_model(form: SSLCertificateForm, cert: SSLCertificate) -> SSLCertificate:
    target_type, target, target_name, _client = resolve_ssl_target(form.target_ref.data)
    cert.common_name = target_name
    cert.provider = form.provider.data
    cert.status = form.status.data
    cert.auto_renew = form.auto_renew.data
    cert.certificate_path = form.certificate_path.data or None
    cert.private_key_path = form.private_key_path.data or None
    bind_certificate_target(cert, target_type, target)
    return cert


def _safe_certificates(client_id: int | None = None) -> list[SSLCertificate]:
    try:
        certificates = SSLCertificate.query.order_by(SSLCertificate.created_at.desc()).all()
    except SQLAlchemyError:
        return []
    if client_id is None:
        return certificates
    return [cert for cert in certificates if cert.client_id == client_id]


@ssl_bp.route("/admin/ssl")
@login_required
@roles_required("administrator")
def admin_ssl():
    certificates = _safe_certificates()
    return render_template("ssl/admin_ssl.html", certificates=certificates)


@ssl_bp.route("/admin/ssl/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_create():
    form = SSLCertificateForm()
    _populate_form(form)
    if not _ensure_targets_available(form):
        return redirect(url_for("ssl.admin_ssl"))
    if form.validate_on_submit():
        try:
            _target_type, target, _target_name, _client = resolve_ssl_target(form.target_ref.data)
            if getattr(target, "ssl_certificate", None) is not None:
                flash("Dla tej witryny istnieje juz certyfikat SSL.", "warning")
                return redirect(url_for("ssl.admin_edit", cert_id=target.ssl_certificate.id))
            cert = _cert_form_to_model(form, SSLCertificate(metadata_json={}))
            db.session.add(cert)
            db.session.commit()
            flash("Certyfikat SSL zostal zapisany.", "success")
            return redirect(url_for("ssl.admin_ssl"))
        except (SSLServiceError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("ssl/ssl_form.html", form=form, title="Nowy certyfikat")


@ssl_bp.route("/admin/ssl/<int:cert_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_edit(cert_id: int):
    cert = SSLCertificate.query.get_or_404(cert_id)
    target_ref = _form_target_ref(cert)
    if target_ref is None:
        flash("Certyfikat nie ma poprawnie przypisanej witryny.", "danger")
        return redirect(url_for("ssl.admin_ssl"))
    form = SSLCertificateForm(obj=cert)
    _populate_form(form, current_target_ref=target_ref)
    if request.method == "GET":
        form.target_ref.data = target_ref
    if form.validate_on_submit():
        try:
            _cert_form_to_model(form, cert)
            db.session.commit()
            flash("Certyfikat zostal zaktualizowany.", "success")
            return redirect(url_for("ssl.admin_ssl"))
        except (SSLServiceError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("ssl/ssl_form.html", form=form, title=f"Edycja certyfikatu {cert.target_name}")


@ssl_bp.route("/admin/ssl/<int:cert_id>/issue", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_issue(cert_id: int):
    cert = SSLCertificate.query.get_or_404(cert_id)
    try:
        issue_certificate(cert, actor=current_user)
        db.session.commit()
        flash(f"Wygenerowano certyfikat dla {cert.target_name}.", "success")
    except SSLServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssl.admin_ssl"))


@ssl_bp.route("/admin/ssl/<int:cert_id>/renew", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_renew(cert_id: int):
    cert = SSLCertificate.query.get_or_404(cert_id)
    try:
        renew_certificate(cert, actor=current_user)
        db.session.commit()
        flash(f"Odnowiono certyfikat dla {cert.target_name}.", "success")
    except SSLServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssl.admin_ssl"))


@ssl_bp.route("/client/ssl")
@login_required
@roles_required("client")
@active_account_required
def client_ssl():
    client = current_client()
    certificates = _safe_certificates(client.id)
    return render_template("ssl/client_ssl.html", certificates=certificates)


@ssl_bp.route("/client/ssl/new", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_create():
    client = current_client()
    form = SSLCertificateForm()
    _populate_form(form, client.id)
    if not _ensure_targets_available(form):
        return redirect(url_for("ssl.client_ssl"))
    if form.validate_on_submit():
        try:
            _target_type, target, _target_name, _resolved_client = resolve_ssl_target(form.target_ref.data)
            if getattr(target, "ssl_certificate", None) is not None:
                flash("Dla tej witryny istnieje juz certyfikat SSL.", "warning")
                return redirect(url_for("ssl.client_edit", cert_id=target.ssl_certificate.id))
            cert = _cert_form_to_model(form, SSLCertificate(metadata_json={}))
            if cert.client_id != client.id:
                flash("Nieprawidlowy wybor witryny.", "danger")
                return redirect(url_for("ssl.client_ssl"))
            db.session.add(cert)
            db.session.commit()
            flash("Certyfikat SSL zostal zapisany.", "success")
            return redirect(url_for("ssl.client_ssl"))
        except (SSLServiceError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("ssl/ssl_form.html", form=form, title="Nowy certyfikat SSL")


@ssl_bp.route("/client/ssl/<int:cert_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_edit(cert_id: int):
    cert = owned_or_404(SSLCertificate, cert_id)
    target_ref = _form_target_ref(cert)
    if target_ref is None:
        flash("Certyfikat nie ma poprawnie przypisanej witryny.", "danger")
        return redirect(url_for("ssl.client_ssl"))
    form = SSLCertificateForm(obj=cert)
    _populate_form(form, cert.client_id, current_target_ref=target_ref)
    if request.method == "GET":
        form.target_ref.data = target_ref
    if form.validate_on_submit():
        try:
            _cert_form_to_model(form, cert)
            if cert.client_id != current_client().id:
                flash("Nieprawidlowy wybor witryny.", "danger")
                return redirect(url_for("ssl.client_ssl"))
            db.session.commit()
            flash("Certyfikat zostal zaktualizowany.", "success")
            return redirect(url_for("ssl.client_ssl"))
        except (SSLServiceError, IntegrityError) as exc:
            db.session.rollback()
            flash(str(exc), "danger")
    return render_template("ssl/ssl_form.html", form=form, title=f"Edycja certyfikatu {cert.target_name}")


@ssl_bp.route("/client/ssl/<int:cert_id>/issue", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_issue(cert_id: int):
    cert = owned_or_404(SSLCertificate, cert_id)
    try:
        issue_certificate(cert, actor=current_user)
        db.session.commit()
        flash(f"Wygenerowano certyfikat dla {cert.target_name}.", "success")
    except SSLServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssl.client_ssl"))


@ssl_bp.route("/client/ssl/<int:cert_id>/renew", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_renew(cert_id: int):
    cert = owned_or_404(SSLCertificate, cert_id)
    try:
        renew_certificate(cert, actor=current_user)
        db.session.commit()
        flash(f"Odnowiono certyfikat dla {cert.target_name}.", "success")
    except SSLServiceError as exc:
        db.session.rollback()
        flash(str(exc), "danger")
    return redirect(url_for("ssl.client_ssl"))
