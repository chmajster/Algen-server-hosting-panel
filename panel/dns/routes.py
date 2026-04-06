from __future__ import annotations

import ipaddress

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import login_required

from panel.extensions import db
from panel.forms.services import DNSRecordForm, DNSZoneForm
from panel.models import Client, DNSRecord, DNSZone
from panel.services.audit import log_activity
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import client_choices, current_client, domain_choices, owned_or_404, zone_choices


dns_bp = Blueprint("dns", __name__)


def _populate_zone_form(form: DNSZoneForm):
    form.client_id.choices = client_choices()
    selected_client_id = form.client_id.data or (form.client_id.choices[0][0] if form.client_id.choices else None)
    form.domain_id.choices = domain_choices(selected_client_id)


def _populate_record_form(form: DNSRecordForm, client_id: int | None = None):
    form.zone_id.choices = zone_choices(client_id)


def _validate_dns_record(form: DNSRecordForm) -> str | None:
    record_type = form.type.data
    value = form.value.data.strip()
    if record_type == "A":
        try:
            ipaddress.IPv4Address(value)
        except ValueError:
            return "Nieprawidłowy adres IPv4."
    if record_type == "AAAA":
        try:
            ipaddress.IPv6Address(value)
        except ValueError:
            return "Nieprawidłowy adres IPv6."
    if record_type in {"CNAME", "MX", "NS"} and "." not in value:
        return "Wartość rekordu musi wyglądać jak hostname."
    return None


@dns_bp.route("/admin/dns")
@login_required
@roles_required("administrator")
def admin_dns():
    zones = DNSZone.query.order_by(DNSZone.created_at.desc()).all()
    records = DNSRecord.query.order_by(DNSRecord.created_at.desc()).limit(50).all()
    return render_template("dns/admin_dns.html", zones=zones, records=records)


@dns_bp.route("/admin/dns/zones/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_zone_create():
    form = DNSZoneForm()
    _populate_zone_form(form)
    if form.validate_on_submit():
        client = Client.query.get_or_404(form.client_id.data)
        zone = DNSZone(
            client=client,
            domain_id=form.domain_id.data,
            name=form.name.data.lower(),
            default_ttl=form.default_ttl.data,
            is_active=form.is_active.data,
        )
        db.session.add(zone)
        log_activity("dns.zone_create", "dns_zone", f"Utworzono strefę {zone.name}", entity_id=zone.name, client=client)
        db.session.commit()
        flash("Strefa DNS została utworzona.", "success")
        return redirect(url_for("dns.admin_dns"))
    return render_template("dns/zone_form.html", form=form, title="Nowa strefa DNS")


@dns_bp.route("/admin/dns/records/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_record_create():
    form = DNSRecordForm()
    _populate_record_form(form)
    if form.validate_on_submit():
        error = _validate_dns_record(form)
        if error:
            flash(error, "danger")
            return render_template("dns/record_form.html", form=form, title="Nowy rekord DNS")
        zone = DNSZone.query.get_or_404(form.zone_id.data)
        record = DNSRecord(
            zone=zone,
            name=form.name.data,
            type=form.type.data,
            value=form.value.data,
            priority=form.priority.data,
            ttl=form.ttl.data,
            disabled=form.disabled.data,
        )
        db.session.add(record)
        log_activity("dns.record_create", "dns_record", f"Utworzono rekord {record.type} {record.name}", entity_id=record.id, client=zone.client)
        db.session.commit()
        flash("Rekord DNS został utworzony.", "success")
        return redirect(url_for("dns.admin_dns"))
    return render_template("dns/record_form.html", form=form, title="Nowy rekord DNS")


@dns_bp.route("/client/dns")
@login_required
@roles_required("client")
@active_account_required
def client_dns():
    client = current_client()
    return render_template("dns/client_dns.html", zones=client.dns_zones, allow_dns_management=client.allow_dns_management)


@dns_bp.route("/client/dns/records/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_record_edit(record_id: int):
    record = owned_or_404(DNSRecord, record_id)
    client = current_client()
    if not client.allow_dns_management:
        flash("Zarządzanie DNS jest wyłączone dla tego konta.", "danger")
        return redirect(url_for("dns.client_dns"))
    form = DNSRecordForm(obj=record)
    _populate_record_form(form, client.id)
    if form.validate_on_submit():
        error = _validate_dns_record(form)
        if error:
            flash(error, "danger")
            return render_template("dns/record_form.html", form=form, title="Edycja rekordu DNS")
        record.name = form.name.data
        record.type = form.type.data
        record.value = form.value.data
        record.priority = form.priority.data
        record.ttl = form.ttl.data
        record.disabled = form.disabled.data
        log_activity("dns.client_record_edit", "dns_record", f"Klient zaktualizował rekord {record.name}", entity_id=record.id, client=record.zone.client)
        db.session.commit()
        flash("Rekord DNS został zapisany.", "success")
        return redirect(url_for("dns.client_dns"))
    return render_template("dns/record_form.html", form=form, title="Edycja rekordu DNS")
