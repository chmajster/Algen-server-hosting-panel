from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from panel.extensions import db
from panel.forms.services import SSLCertificateForm
from panel.models import Domain, SSLCertificate
from panel.services.audit import log_activity
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import current_client, domain_choices, owned_or_404


ssl_bp = Blueprint("ssl", __name__)


def _populate_form(form: SSLCertificateForm, client_id: int | None = None):
    form.domain_id.choices = domain_choices(client_id)


@ssl_bp.route("/admin/ssl")
@login_required
@roles_required("administrator")
def admin_ssl():
    return render_template("ssl/admin_ssl.html", certificates=SSLCertificate.query.order_by(SSLCertificate.created_at.desc()).all())


@ssl_bp.route("/admin/ssl/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_create():
    form = SSLCertificateForm()
    _populate_form(form)
    if form.validate_on_submit():
        domain = Domain.query.get_or_404(form.domain_id.data)
        cert = SSLCertificate(
            domain=domain,
            provider=form.provider.data,
            status=form.status.data,
            auto_renew=form.auto_renew.data,
            certificate_path=form.certificate_path.data,
            private_key_path=form.private_key_path.data,
            valid_from=datetime.utcnow(),
            valid_until=datetime.utcnow() + timedelta(days=90),
            metadata_json={"renewal_provider": form.provider.data},
        )
        domain.ssl_enabled = True
        db.session.add(cert)
        log_activity("ssl.create", "ssl_certificate", f"Utworzono certyfikat dla {domain.name}", entity_id=domain.name, client=domain.client)
        db.session.commit()
        flash("Certyfikat SSL został zapisany.", "success")
        return redirect(url_for("ssl.admin_ssl"))
    return render_template("ssl/ssl_form.html", form=form, title="Nowy certyfikat")


@ssl_bp.route("/client/ssl")
@login_required
@roles_required("client")
@active_account_required
def client_ssl():
    client = current_client()
    return render_template("ssl/client_ssl.html", certificates=[domain.ssl_certificate for domain in client.domains if domain.ssl_certificate])


@ssl_bp.route("/client/ssl/<int:cert_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_edit(cert_id: int):
    cert = owned_or_404(SSLCertificate, cert_id)
    form = SSLCertificateForm(obj=cert)
    _populate_form(form, cert.domain.client_id)
    if form.validate_on_submit():
        cert.provider = form.provider.data
        cert.status = form.status.data
        cert.auto_renew = form.auto_renew.data
        cert.certificate_path = form.certificate_path.data
        cert.private_key_path = form.private_key_path.data
        log_activity("ssl.client_edit", "ssl_certificate", f"Klient zaktualizował certyfikat {cert.domain.name}", entity_id=cert.id, client=cert.domain.client)
        db.session.commit()
        flash("Certyfikat został zaktualizowany.", "success")
        return redirect(url_for("ssl.client_ssl"))
    return render_template("ssl/ssl_form.html", form=form, title=f"Edycja certyfikatu {cert.domain.name}")
