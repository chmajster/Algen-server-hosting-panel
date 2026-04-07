from __future__ import annotations

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.forms.webhooks import WebhookEndpointForm
from panel.models import Client, WebhookDelivery, WebhookEndpoint
from panel.services.audit import log_activity
from panel.services.webhooks import (
    WEBHOOK_EVENT_TYPES,
    deliver_to_endpoint,
    normalize_event_types,
    process_webhook_retries,
    replay_webhook_delivery,
)
from panel.utils.decorators import roles_required


webhooks_bp = Blueprint("webhooks", __name__)


def _client_scope_choices() -> list[tuple[int, str]]:
    choices = [(0, "Globalny (wszyscy klienci)")]
    clients = Client.query.order_by(Client.id.asc()).all()
    choices.extend((client.id, client.user.username) for client in clients if client.user is not None)
    return choices


def _event_choices() -> list[tuple[str, str]]:
    return [(value, label) for value, label in WEBHOOK_EVENT_TYPES]


def _populate_form(form: WebhookEndpointForm) -> None:
    form.client_id.choices = _client_scope_choices()
    form.event_types.choices = _event_choices()


@webhooks_bp.route("/admin/webhooks")
@login_required
@roles_required("administrator")
def index():
    endpoints = WebhookEndpoint.query.order_by(WebhookEndpoint.created_at.desc()).all()
    deliveries = WebhookDelivery.query.order_by(WebhookDelivery.attempted_at.desc(), WebhookDelivery.id.desc()).limit(100).all()
    return render_template("admin/webhooks.html", endpoints=endpoints, deliveries=deliveries, event_types=WEBHOOK_EVENT_TYPES)


@webhooks_bp.route("/admin/webhooks/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def create():
    form = WebhookEndpointForm()
    _populate_form(form)

    if form.validate_on_submit():
        endpoint = WebhookEndpoint(
            name=(form.name.data or "").strip(),
            target_url=(form.target_url.data or "").strip(),
            secret=(form.secret.data or "").strip() or None,
            client_id=form.client_id.data or None,
            event_types_json=normalize_event_types(form.event_types.data),
            is_active=bool(form.is_active.data),
            created_by=current_user,
        )
        db.session.add(endpoint)
        log_activity(
            "webhooks.create",
            "webhook_endpoint",
            f"Utworzono webhook {endpoint.name}",
            entity_id=endpoint.id,
            actor=current_user,
            client=endpoint.client,
            metadata={"target_url": endpoint.target_url, "event_types": endpoint.event_types_json},
        )
        db.session.commit()
        flash("Webhook zostal utworzony.", "success")
        return redirect(url_for("webhooks.index"))

    return render_template("admin/webhook_form.html", form=form, title="Nowy webhook")


@webhooks_bp.route("/admin/webhooks/<int:endpoint_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def edit(endpoint_id: int):
    endpoint = WebhookEndpoint.query.get_or_404(endpoint_id)
    form = WebhookEndpointForm(obj=endpoint)
    _populate_form(form)

    if request.method == "GET":
        form.client_id.data = endpoint.client_id or 0
        form.event_types.data = list(endpoint.event_types_json or [])

    if form.validate_on_submit():
        endpoint.name = (form.name.data or "").strip()
        endpoint.target_url = (form.target_url.data or "").strip()
        endpoint.secret = (form.secret.data or "").strip() or None
        endpoint.client_id = form.client_id.data or None
        endpoint.event_types_json = normalize_event_types(form.event_types.data)
        endpoint.is_active = bool(form.is_active.data)
        log_activity(
            "webhooks.edit",
            "webhook_endpoint",
            f"Zmieniono webhook {endpoint.name}",
            entity_id=endpoint.id,
            actor=current_user,
            client=endpoint.client,
            metadata={"target_url": endpoint.target_url, "event_types": endpoint.event_types_json, "active": endpoint.is_active},
        )
        db.session.commit()
        flash("Webhook zostal zaktualizowany.", "success")
        return redirect(url_for("webhooks.index"))

    return render_template("admin/webhook_form.html", form=form, title=f"Edycja webhooka {endpoint.name}")


@webhooks_bp.route("/admin/webhooks/<int:endpoint_id>/delete", methods=["POST"])
@login_required
@roles_required("administrator")
def delete(endpoint_id: int):
    endpoint = WebhookEndpoint.query.get_or_404(endpoint_id)
    name = endpoint.name
    db.session.delete(endpoint)
    log_activity(
        "webhooks.delete",
        "webhook_endpoint",
        f"Usunieto webhook {name}",
        entity_id=endpoint_id,
        actor=current_user,
    )
    db.session.commit()
    flash("Webhook zostal usuniety.", "warning")
    return redirect(url_for("webhooks.index"))


@webhooks_bp.route("/admin/webhooks/<int:endpoint_id>/send-test", methods=["POST"])
@login_required
@roles_required("administrator")
def send_test(endpoint_id: int):
    endpoint = WebhookEndpoint.query.get_or_404(endpoint_id)
    configured_events = normalize_event_types(endpoint.event_types_json)
    if configured_events:
        event_type = configured_events[0]
    else:
        event_type = WEBHOOK_EVENT_TYPES[0][0]

    delivery = deliver_to_endpoint(
        endpoint,
        event_type,
        {
            "test": True,
            "event": event_type,
            "endpoint_id": endpoint.id,
            "endpoint_name": endpoint.name,
        },
        auto_commit=True,
    )
    log_activity(
        "webhooks.test",
        "webhook_delivery",
        f"Wyslano test webhooka {endpoint.name}",
        entity_id=delivery.id,
        actor=current_user,
        client=endpoint.client,
        metadata={
            "status_code": delivery.status_code,
            "success": delivery.success,
            "response_excerpt": delivery.response_excerpt,
        },
        success=delivery.success,
    )
    db.session.commit()

    if delivery.success:
        flash(f"Webhook testowy wyslany poprawnie (HTTP {delivery.status_code}).", "success")
    else:
        flash(f"Webhook testowy nie powiodl sie (HTTP {delivery.status_code or 'blad polaczenia'}).", "warning")
    return redirect(url_for("webhooks.index"))


@webhooks_bp.route("/admin/webhooks/deliveries/<int:delivery_id>/replay", methods=["POST"])
@login_required
@roles_required("administrator")
def replay_delivery(delivery_id: int):
    delivery = WebhookDelivery.query.get_or_404(delivery_id)
    replayed = replay_webhook_delivery(delivery)
    log_activity(
        "webhooks.replay",
        "webhook_delivery",
        f"Ponowiono webhook delivery #{delivery.id}",
        entity_id=delivery.id,
        actor=current_user,
        client=delivery.endpoint.client,
        metadata={
            "status_code": replayed.status_code,
            "success": replayed.success,
            "attempt_count": replayed.attempt_count,
        },
        success=replayed.success,
    )
    db.session.commit()

    if replayed.success:
        flash("Ponowienie webhooka zakonczone sukcesem.", "success")
    else:
        flash("Ponowienie webhooka nie powiodlo sie.", "warning")
    return redirect(url_for("webhooks.index"))


@webhooks_bp.route("/admin/webhooks/retries/process", methods=["POST"])
@login_required
@roles_required("administrator")
def process_retries():
    summary = process_webhook_retries(limit=100)
    log_activity(
        "webhooks.retry_process",
        "webhook_delivery",
        "Przetworzono kolejke retry webhookow",
        actor=current_user,
        metadata=summary,
    )
    db.session.commit()
    flash(
        f"Retry webhookow: przetworzono={summary['processed']}, sukces={summary['delivered']}, dead-letter={summary['dead_lettered']}.",
        "info",
    )
    return redirect(url_for("webhooks.index"))
