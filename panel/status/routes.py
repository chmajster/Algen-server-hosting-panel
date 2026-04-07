from __future__ import annotations

from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_

from panel.extensions import db
from panel.forms.status import STATUS_STATE_CHOICES, StatusEventForm
from panel.models import StatusEvent
from panel.services.audit import log_activity
from panel.services.webhooks import dispatch_webhook_event
from panel.utils.decorators import active_account_required, roles_required


status_bp = Blueprint("status", __name__)


def _parse_components(raw: str | None) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _serialize_components(values: list[str] | None) -> str:
    return ", ".join(values or [])


def _state_weight(state: str) -> int:
    order = {
        "operational": 0,
        "degraded_performance": 1,
        "partial_outage": 2,
        "major_outage": 3,
        "maintenance": 4,
        "resolved": 0,
    }
    return order.get(state, 0)


def _current_public_events() -> list[StatusEvent]:
    now = datetime.utcnow()
    return (
        StatusEvent.query.filter(
            StatusEvent.is_public.is_(True),
            StatusEvent.starts_at <= now,
            or_(StatusEvent.ends_at.is_(None), StatusEvent.ends_at >= now),
            StatusEvent.state != "resolved",
        )
        .order_by(StatusEvent.starts_at.desc(), StatusEvent.id.desc())
        .all()
    )


@status_bp.route("/admin/status")
@login_required
@roles_required("administrator")
def admin_index():
    events = StatusEvent.query.order_by(StatusEvent.starts_at.desc(), StatusEvent.id.desc()).all()
    return render_template("status/admin_status_events.html", events=events)


@status_bp.route("/admin/status/new", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_create():
    form = StatusEventForm()
    if form.validate_on_submit():
        event = StatusEvent(
            event_type=form.event_type.data,
            state=form.state.data,
            title=(form.title.data or "").strip(),
            public_message=(form.public_message.data or "").strip(),
            internal_note=(form.internal_note.data or "").strip() or None,
            affected_components_json=_parse_components(form.affected_components.data),
            starts_at=form.starts_at.data,
            ends_at=form.ends_at.data,
            is_public=bool(form.is_public.data),
            created_by=current_user,
        )
        if event.state == "resolved":
            event.resolved_at = datetime.utcnow()
        db.session.add(event)
        log_activity(
            "status.create",
            "status_event",
            f"Utworzono zdarzenie statusowe {event.title}",
            entity_id=event.id,
            actor=current_user,
            metadata={
                "event_type": event.event_type,
                "state": event.state,
                "is_public": event.is_public,
            },
        )
        dispatch_webhook_event(
            "incident.created",
            {
                "status_event_id": event.id,
                "event_type": event.event_type,
                "state": event.state,
                "title": event.title,
                "starts_at": event.starts_at.isoformat() if event.starts_at else None,
                "ends_at": event.ends_at.isoformat() if event.ends_at else None,
            },
            auto_commit=False,
        )
        db.session.commit()
        flash("Zdarzenie statusowe zostalo utworzone.", "success")
        return redirect(url_for("status.admin_index"))
    return render_template("status/admin_status_form.html", form=form, title="Nowe zdarzenie statusowe")


@status_bp.route("/admin/status/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_edit(event_id: int):
    event = StatusEvent.query.get_or_404(event_id)
    form = StatusEventForm(obj=event)

    if form.validate_on_submit():
        event.event_type = form.event_type.data
        event.state = form.state.data
        event.title = (form.title.data or "").strip()
        event.public_message = (form.public_message.data or "").strip()
        event.internal_note = (form.internal_note.data or "").strip() or None
        event.affected_components_json = _parse_components(form.affected_components.data)
        event.starts_at = form.starts_at.data
        event.ends_at = form.ends_at.data
        event.is_public = bool(form.is_public.data)
        if event.state == "resolved" and event.resolved_at is None:
            event.resolved_at = datetime.utcnow()
        elif event.state != "resolved":
            event.resolved_at = None

        log_activity(
            "status.edit",
            "status_event",
            f"Zaktualizowano zdarzenie statusowe {event.title}",
            entity_id=event.id,
            actor=current_user,
            metadata={
                "event_type": event.event_type,
                "state": event.state,
                "is_public": event.is_public,
            },
        )
        db.session.commit()
        flash("Zdarzenie statusowe zostalo zaktualizowane.", "success")
        return redirect(url_for("status.admin_index"))

    if form.affected_components.data is None:
        form.affected_components.data = _serialize_components(event.affected_components_json)
    return render_template("status/admin_status_form.html", form=form, title=f"Edycja: {event.title}")


@status_bp.route("/admin/status/<int:event_id>/resolve", methods=["POST"])
@login_required
@roles_required("administrator")
def admin_resolve(event_id: int):
    event = StatusEvent.query.get_or_404(event_id)
    event.state = "resolved"
    event.resolved_at = datetime.utcnow()
    if event.ends_at is None:
        event.ends_at = datetime.utcnow()

    log_activity(
        "status.resolve",
        "status_event",
        f"Rozwiazano zdarzenie statusowe {event.title}",
        entity_id=event.id,
        actor=current_user,
    )
    db.session.commit()
    flash("Zdarzenie statusowe oznaczono jako resolved.", "success")
    return redirect(url_for("status.admin_index"))


@status_bp.route("/client/status")
@login_required
@roles_required("client")
@active_account_required
def client_status_page():
    current_events = _current_public_events()
    maintenance_events = [item for item in current_events if item.event_type == "maintenance" or item.state == "maintenance"]
    incident_events = [item for item in current_events if item.event_type != "maintenance" and item.state != "maintenance"]

    history = (
        StatusEvent.query.filter(StatusEvent.is_public.is_(True))
        .order_by(StatusEvent.starts_at.desc(), StatusEvent.id.desc())
        .limit(100)
        .all()
    )

    overall_state = "operational"
    if maintenance_events:
        overall_state = "maintenance"
    if incident_events:
        overall_state = max((item.state for item in incident_events), key=_state_weight)

    return render_template(
        "status/client_status_page.html",
        overall_state=overall_state,
        incidents=incident_events,
        maintenance=maintenance_events,
        history=history,
        state_choices=dict(STATUS_STATE_CHOICES),
    )
