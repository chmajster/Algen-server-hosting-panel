from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from panel.extensions import db, get_client_ip, limiter
from panel.forms.auth import LoginForm
from panel.models import User
from panel.services.audit import log_activity


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def _safe_next_url(value: str | None) -> str | None:
    target = (value or "").strip()
    if not target:
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc:
        return None
    if not target.startswith("/") or target.startswith("//"):
        return None
    return target


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config.get("LOGIN_RATELIMIT", "10 per 10 minutes"),
    methods=["POST"],
    error_message="Zbyt wiele prob logowania. Odczekaj chwile i sprobuj ponownie.",
)
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard" if current_user.has_role("administrator") else "client.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        identity = (form.username.data or "").strip()
        user = User.query.filter(
            or_(User.username == identity, User.email == identity)
        ).first()
        if (
            user
            and user.check_password(form.password.data)
            and user.is_active_account
            and user.status in {"active", "overdue", "suspended_financial"}
        ):
            login_user(user, remember=form.remember_me.data)
            user.last_login_at = datetime.utcnow()
            user.last_login_ip = get_client_ip()
            log_activity("auth.login", "user", "Udane logowanie", entity_id=user.id, actor=user)
            db.session.commit()
            flash("Zalogowano pomyślnie.", "success")
            next_url = _safe_next_url(request.args.get("next"))
            return redirect(
                next_url
                or url_for("admin.dashboard" if user.has_role("administrator") else "client.dashboard")
            )
        flash("Nieprawidłowe dane logowania.", "danger")
        log_activity("auth.login_failed", "user", "Nieudana próba logowania", success=False)
        db.session.commit()
    return render_template("auth/login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    user_id = current_user.id
    log_activity("auth.logout", "user", "Wylogowanie użytkownika", entity_id=user_id, actor=current_user)
    db.session.commit()
    logout_user()
    flash("Wylogowano.", "info")
    return redirect(url_for("auth.login"))
