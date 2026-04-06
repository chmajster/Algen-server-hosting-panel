from __future__ import annotations

from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from panel.extensions import db, limiter
from panel.forms.auth import LoginForm
from panel.models import User
from panel.services.audit import log_activity


auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("5/minute")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard" if current_user.has_role("administrator") else "client.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter(
            or_(User.username == form.username.data, User.email == form.username.data)
        ).first()
        if (
            user
            and user.check_password(form.password.data)
            and user.is_active_account
            and user.status in {"active", "overdue", "suspended_financial"}
        ):
            login_user(user)
            user.last_login_at = datetime.utcnow()
            user.last_login_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            log_activity("auth.login", "user", "Udane logowanie", entity_id=user.id, actor=user)
            db.session.commit()
            flash("Zalogowano pomyślnie.", "success")
            return redirect(
                request.args.get("next")
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
