from __future__ import annotations

from datetime import datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required

from panel.extensions import db
from panel.forms.tickets import TicketAdminUpdateForm, TicketReplyForm, TicketCreateForm
from panel.models import Role, Ticket, TicketAttachment, TicketMessage, User
from panel.services.audit import log_activity
from panel.services.ticket_attachments import resolve_attachment_path, save_ticket_attachment
from panel.services.ticket_notifications import (
    notify_client_staff_reply,
    notify_staff_client_reply,
    notify_staff_new_ticket,
)
from panel.services.ticket_sla import apply_ticket_sla_defaults, mark_staff_first_response
from panel.services.webhooks import dispatch_webhook_event
from panel.utils.decorators import active_account_required, roles_required
from panel.utils.query import current_client


tickets_bp = Blueprint("tickets", __name__)

STAFF_ROLE_NAMES = ("administrator", "operator")


def _staff_user_choices() -> list[tuple[int, str]]:
    users = (
        User.query.join(Role)
        .filter(Role.name.in_(STAFF_ROLE_NAMES))
        .order_by(User.username.asc())
        .all()
    )
    return [(0, "Nieprzypisany")] + [(user.id, f"{user.username} ({user.role.name})") for user in users]


def _ticket_attachment_from_request() -> object | None:
    attachment = request.files.get("attachment")
    if attachment is None or not getattr(attachment, "filename", ""):
        return None
    return attachment


def _add_attachment_if_present(*, ticket: Ticket, message: TicketMessage):
    attachment_file = _ticket_attachment_from_request()
    if attachment_file is None:
        return None
    attachment = save_ticket_attachment(
        ticket=ticket,
        message=message,
        uploaded_by=current_user,
        file_storage=attachment_file,
    )
    if attachment is not None:
        db.session.add(attachment)
    return attachment


@tickets_bp.route("/client/tickets")
@login_required
@roles_required("client")
@active_account_required
def client_tickets():
    client = current_client()
    tickets = Ticket.query.filter_by(client_id=client.id).order_by(Ticket.updated_at.desc()).all()
    return render_template("tickets/client_tickets.html", tickets=tickets)


@tickets_bp.route("/client/tickets/new", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_ticket_create():
    client = current_client()
    form = TicketCreateForm()
    if form.validate_on_submit():
        now = datetime.utcnow()
        ticket = Ticket(
            client=client,
            created_by=current_user,
            subject=(form.subject.data or "").strip(),
            category=form.category.data,
            priority=form.priority.data,
            status="open",
            last_message_at=now,
            metadata_json={"source": "client_panel"},
        )
        apply_ticket_sla_defaults(ticket, now=now)
        message = TicketMessage(
            ticket=ticket,
            author=current_user,
            message=(form.message.data or "").strip(),
        )
        db.session.add(ticket)
        db.session.add(message)
        db.session.flush()
        try:
            attachment = _add_attachment_if_present(ticket=ticket, message=message)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template("tickets/ticket_form.html", form=form, title="Nowy ticket")
        log_activity(
            "tickets.create",
            "ticket",
            f"Utworzono ticket {ticket.display_number}",
            entity_id=ticket.id,
            client=client,
            actor=current_user,
            metadata={
                "ticket": ticket.display_number,
                "attachment": attachment.original_filename if attachment else None,
            },
        )
        db.session.commit()
        notify_staff_new_ticket(ticket)
        dispatch_webhook_event(
            "ticket.created",
            {
                "ticket_id": ticket.id,
                "ticket": ticket.display_number,
                "subject": ticket.subject,
                "status": ticket.status,
                "priority": ticket.priority,
                "category": ticket.category,
                "client": client.user.username if client.user else None,
                "attachment": attachment.original_filename if attachment else None,
            },
            client=client,
        )
        flash("Ticket zostal utworzony.", "success")
        return redirect(url_for("tickets.client_ticket_view", ticket_id=ticket.id))
    return render_template("tickets/ticket_form.html", form=form, title="Nowy ticket")


@tickets_bp.route("/client/tickets/<int:ticket_id>", methods=["GET", "POST"])
@login_required
@roles_required("client")
@active_account_required
def client_ticket_view(ticket_id: int):
    client = current_client()
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.client_id != client.id:
        abort(404)

    reply_form = TicketReplyForm(prefix="reply")
    if reply_form.validate_on_submit():
        message = TicketMessage(
            ticket=ticket,
            author=current_user,
            message=(reply_form.message.data or "").strip(),
        )
        ticket.status = "open"
        ticket.closed_at = None
        ticket.last_message_at = datetime.utcnow()
        db.session.add(message)
        db.session.flush()
        try:
            attachment = _add_attachment_if_present(ticket=ticket, message=message)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template(
                "tickets/client_ticket_thread.html",
                ticket=ticket,
                reply_form=reply_form,
                title=f"Ticket {ticket.display_number}",
            )
        log_activity(
            "tickets.reply_client",
            "ticket",
            f"Klient odpowiedzial w ticketcie {ticket.display_number}",
            entity_id=ticket.id,
            client=client,
            actor=current_user,
            metadata={"attachment": attachment.original_filename if attachment else None},
        )
        db.session.commit()
        notify_staff_client_reply(ticket, message)
        dispatch_webhook_event(
            "ticket.client_reply",
            {
                "ticket_id": ticket.id,
                "ticket": ticket.display_number,
                "message_id": message.id,
                "status": ticket.status,
                "priority": ticket.priority,
                "client": client.user.username if client.user else None,
                "attachment": attachment.original_filename if attachment else None,
            },
            client=client,
        )
        flash("Wyslano odpowiedz w ticketcie.", "success")
        return redirect(url_for("tickets.client_ticket_view", ticket_id=ticket.id))

    return render_template(
        "tickets/client_ticket_thread.html",
        ticket=ticket,
        reply_form=reply_form,
        title=f"Ticket {ticket.display_number}",
    )


@tickets_bp.route("/client/tickets/<int:ticket_id>/close", methods=["POST"])
@login_required
@roles_required("client")
@active_account_required
def client_ticket_close(ticket_id: int):
    client = current_client()
    ticket = Ticket.query.get_or_404(ticket_id)
    if ticket.client_id != client.id:
        abort(404)
    if ticket.status != "closed":
        ticket.status = "closed"
        ticket.closed_at = datetime.utcnow()
        ticket.last_message_at = datetime.utcnow()
        log_activity(
            "tickets.close_client",
            "ticket",
            f"Klient zamknal ticket {ticket.display_number}",
            entity_id=ticket.id,
            client=client,
            actor=current_user,
        )
        db.session.commit()
        flash("Ticket zostal zamkniety.", "info")
    return redirect(url_for("tickets.client_ticket_view", ticket_id=ticket.id))


@tickets_bp.route("/admin/tickets")
@login_required
@roles_required("administrator")
def admin_tickets():
    status_filter = (request.args.get("status") or "").strip().lower()
    query = Ticket.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    tickets = query.order_by(Ticket.updated_at.desc()).all()
    return render_template("tickets/admin_tickets.html", tickets=tickets, status_filter=status_filter)


@tickets_bp.route("/admin/tickets/<int:ticket_id>", methods=["GET", "POST"])
@login_required
@roles_required("administrator")
def admin_ticket_view(ticket_id: int):
    ticket = Ticket.query.get_or_404(ticket_id)

    reply_form = TicketReplyForm(prefix="reply")
    update_form = TicketAdminUpdateForm(prefix="update")
    update_form.assigned_to_user_id.choices = _staff_user_choices()

    if request.method == "POST" and reply_form.submit.data and reply_form.validate_on_submit():
        message = TicketMessage(
            ticket=ticket,
            author=current_user,
            message=(reply_form.message.data or "").strip(),
        )
        ticket.status = "answered"
        mark_staff_first_response(ticket)
        ticket.closed_at = None
        ticket.last_message_at = datetime.utcnow()
        db.session.add(message)
        db.session.flush()
        try:
            attachment = _add_attachment_if_present(ticket=ticket, message=message)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "danger")
            return render_template(
                "tickets/admin_ticket_thread.html",
                ticket=ticket,
                reply_form=reply_form,
                update_form=update_form,
                title=f"Ticket {ticket.display_number}",
            )
        log_activity(
            "tickets.reply_staff",
            "ticket",
            f"Dodano odpowiedz staffu do ticketu {ticket.display_number}",
            entity_id=ticket.id,
            client=ticket.client,
            actor=current_user,
            metadata={"attachment": attachment.original_filename if attachment else None},
        )
        db.session.commit()
        notify_client_staff_reply(ticket, message)
        dispatch_webhook_event(
            "ticket.staff_reply",
            {
                "ticket_id": ticket.id,
                "ticket": ticket.display_number,
                "message_id": message.id,
                "status": ticket.status,
                "priority": ticket.priority,
                "staff": current_user.username,
                "attachment": attachment.original_filename if attachment else None,
            },
            client=ticket.client,
        )
        flash("Odpowiedz zostala wyslana.", "success")
        return redirect(url_for("tickets.admin_ticket_view", ticket_id=ticket.id))

    if request.method == "POST" and update_form.submit.data and update_form.validate_on_submit():
        ticket.status = update_form.status.data
        ticket.priority = update_form.priority.data
        ticket.assigned_to_user_id = update_form.assigned_to_user_id.data or None
        if ticket.status == "closed":
            ticket.closed_at = datetime.utcnow()
        else:
            ticket.closed_at = None
        ticket.last_message_at = datetime.utcnow()
        log_activity(
            "tickets.update_staff",
            "ticket",
            f"Zmieniono ustawienia ticketu {ticket.display_number}",
            entity_id=ticket.id,
            client=ticket.client,
            actor=current_user,
            metadata={
                "status": ticket.status,
                "priority": ticket.priority,
                "assigned_to": ticket.assigned_to_user_id,
            },
        )
        db.session.commit()
        flash("Ustawienia ticketu zapisane.", "success")
        return redirect(url_for("tickets.admin_ticket_view", ticket_id=ticket.id))

    if request.method == "GET":
        update_form.status.data = ticket.status
        update_form.priority.data = ticket.priority
        update_form.assigned_to_user_id.data = ticket.assigned_to_user_id or 0

    return render_template(
        "tickets/admin_ticket_thread.html",
        ticket=ticket,
        reply_form=reply_form,
        update_form=update_form,
        title=f"Ticket {ticket.display_number}",
    )


@tickets_bp.route("/tickets/attachments/<int:attachment_id>/download")
@login_required
def ticket_attachment_download(attachment_id: int):
    attachment = TicketAttachment.query.get_or_404(attachment_id)
    ticket = attachment.ticket

    if not current_user.is_staff:
        if current_user.role is None or current_user.role.name != "client":
            abort(403)
        client = current_client()
        if ticket.client_id != client.id:
            abort(404)

    file_path = resolve_attachment_path(attachment)
    if not file_path.exists() or not file_path.is_file():
        abort(404)

    return send_file(file_path, as_attachment=True, download_name=attachment.original_filename)
