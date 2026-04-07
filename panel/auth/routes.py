from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from panel.extensions import db, get_client_ip, limiter
from panel.forms.auth import (
    LoginForm,
    TwoFactorChallengeForm,
    TwoFactorDisableForm,
    TwoFactorEnableEmailForm,
    TwoFactorEnableTotpForm,
)
from panel.models import User
from panel.services.audit import log_activity
from panel.services.mailer import send_plain_email
from panel.services.two_factor import (
    build_email_code_hash,
    build_totp_uri,
    format_secret_for_display,
    generate_email_code,
    generate_two_factor_secret,
    normalize_totp_code,
    verify_totp_code,
)


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


def _two_factor_available() -> bool:
    return bool(current_app.config.get("TWO_FACTOR_AVAILABLE", False))


def _two_factor_email_enabled() -> bool:
    return bool(current_app.config.get("TWO_FACTOR_EMAIL_ENABLED", True))


def _effective_two_factor_method(user: User) -> str:
    method = (user.two_factor_method or "").strip().lower()
    if method == "email":
        return "email"
    return "totp"


def _mask_email(value: str) -> str:
    local_part, separator, domain = (value or "").partition("@")
    if not separator:
        return value
    if len(local_part) <= 2:
        masked_local = (local_part[:1] or "*") + "*"
    else:
        masked_local = local_part[:2] + "*" * (len(local_part) - 2)
    return f"{masked_local}@{domain}"


def _clear_pending_login_state() -> None:
    session.pop("pending_2fa_user_id", None)
    session.pop("pending_2fa_remember", None)
    session.pop("pending_2fa_next", None)
    session.pop("pending_2fa_method", None)
    session.pop("pending_2fa_email_hash", None)
    session.pop("pending_2fa_email_expires", None)
    session.pop("pending_2fa_email_code_test", None)


def _send_email_two_factor_code(user: User) -> str | None:
    if not user.email:
        return "Konto nie ma skonfigurowanego adresu e-mail."

    code = generate_email_code()
    ttl_seconds = int(current_app.config.get("TWO_FACTOR_EMAIL_CODE_TTL_SECONDS", 300))
    expires_at = datetime.utcnow() + timedelta(seconds=max(ttl_seconds, 60))
    code_hash = build_email_code_hash(
        secret_key=str(current_app.config.get("SECRET_KEY", "")),
        user_id=user.id,
        code=code,
    )

    session["pending_2fa_email_hash"] = code_hash
    session["pending_2fa_email_expires"] = expires_at.isoformat()
    if current_app.config.get("TESTING"):
        session["pending_2fa_email_code_test"] = code

    ttl_minutes = max(1, int(ttl_seconds / 60))
    subject = str(current_app.config.get("TWO_FACTOR_EMAIL_SUBJECT", "Kod logowania 2FA"))
    body = (
        f"Witaj {user.full_name},\n\n"
        f"Kod 2FA do logowania: {code}\n"
        f"Waznosc kodu: {ttl_minutes} min.\n\n"
        "Jesli to nie Ty probowales sie zalogowac, zignoruj ta wiadomosc."
    )
    send_error = send_plain_email(to_email=user.email, subject=subject, body=body)
    if send_error:
        session.pop("pending_2fa_email_hash", None)
        session.pop("pending_2fa_email_expires", None)
        session.pop("pending_2fa_email_code_test", None)
        return send_error
    return None


def _verify_pending_email_code(user: User, code: str | None) -> tuple[bool, str | None]:
    normalized = normalize_totp_code(code)
    if not normalized:
        return False, "Nieprawidlowy kod 2FA."

    code_hash = (session.get("pending_2fa_email_hash") or "").strip()
    expires_raw = (session.get("pending_2fa_email_expires") or "").strip()
    if not code_hash or not expires_raw:
        return False, "Kod e-mail wygasl. Zaloguj sie ponownie."

    try:
        expires_at = datetime.fromisoformat(expires_raw)
    except ValueError:
        return False, "Kod e-mail wygasl. Zaloguj sie ponownie."

    if datetime.utcnow() > expires_at:
        return False, "Kod e-mail wygasl. Zaloguj sie ponownie."

    expected_hash = build_email_code_hash(
        secret_key=str(current_app.config.get("SECRET_KEY", "")),
        user_id=user.id,
        code=normalized,
    )
    if not hmac.compare_digest(expected_hash, code_hash):
        return False, "Nieprawidlowy kod 2FA."

    return True, None


def _post_login_redirect(user: User, next_url: str | None) -> str:
    return next_url or url_for("admin.dashboard" if user.has_role("administrator") else "client.dashboard")


def _complete_login(
    user: User,
    *,
    remember: bool,
    next_url: str | None,
    used_two_factor: bool,
) -> str:
    login_user(user, remember=remember)
    user.last_login_at = datetime.utcnow()
    user.last_login_ip = get_client_ip()
    log_activity(
        "auth.login_2fa" if used_two_factor else "auth.login",
        "user",
        "Udane logowanie z 2FA" if used_two_factor else "Udane logowanie",
        entity_id=user.id,
        actor=user,
        metadata={"two_factor": used_two_factor},
    )
    db.session.commit()
    flash("Zalogowano pomyslnie.", "success")
    return _post_login_redirect(user, next_url)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config.get("LOGIN_RATELIMIT", "10 per 10 minutes"),
    methods=["POST"],
    error_message="Zbyt wiele prob logowania. Odczekaj chwile i sprobuj ponownie.",
)
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard" if current_user.has_role("administrator") else "client.dashboard"))

    if request.method == "GET":
        _clear_pending_login_state()

    form = LoginForm()
    if form.validate_on_submit():
        _clear_pending_login_state()
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
            next_url = _safe_next_url(request.args.get("next"))
            if _two_factor_available() and user.two_factor_enabled:
                method = _effective_two_factor_method(user)
                if method == "totp" and not user.two_factor_secret:
                    flash("2FA Google Authenticator jest niekompletne dla tego konta. Skontaktuj sie z administratorem.", "danger")
                    log_activity(
                        "auth.login_2fa_failed",
                        "user",
                        "Nieudane logowanie: brak sekretu TOTP",
                        entity_id=user.id,
                        actor=user,
                        success=False,
                    )
                    db.session.commit()
                    return render_template("auth/login.html", form=form)

                if method == "email" and not _two_factor_email_enabled():
                    flash("2FA przez e-mail jest obecnie wylaczone przez administratora.", "danger")
                    log_activity(
                        "auth.login_2fa_failed",
                        "user",
                        "Nieudane logowanie: 2FA email wylaczone",
                        entity_id=user.id,
                        actor=user,
                        success=False,
                    )
                    db.session.commit()
                    return render_template("auth/login.html", form=form)

                session["pending_2fa_user_id"] = user.id
                session["pending_2fa_remember"] = bool(form.remember_me.data)
                session["pending_2fa_next"] = next_url or ""
                session["pending_2fa_method"] = method

                if method == "email":
                    send_error = _send_email_two_factor_code(user)
                    if send_error:
                        _clear_pending_login_state()
                        flash(f"Nie udalo sie wyslac kodu 2FA e-mail: {send_error}", "danger")
                        log_activity(
                            "auth.login_2fa_failed",
                            "user",
                            "Nieudane logowanie: blad wysylki kodu 2FA e-mail",
                            entity_id=user.id,
                            actor=user,
                            success=False,
                        )
                        db.session.commit()
                        return render_template("auth/login.html", form=form)
                    flash(f"Wyslano kod 2FA na adres {_mask_email(user.email)}.", "info")
                else:
                    flash("Wprowadz kod 2FA z Google Authenticator, aby dokonczyc logowanie.", "info")
                return redirect(url_for("auth.login_2fa"))
            return redirect(
                _complete_login(
                    user,
                    remember=bool(form.remember_me.data),
                    next_url=next_url,
                    used_two_factor=False,
                )
            )
        flash("Nieprawidlowe dane logowania.", "danger")
        log_activity("auth.login_failed", "user", "Nieudana proba logowania", success=False)
        db.session.commit()
    return render_template("auth/login.html", form=form)


@auth_bp.route("/2fa", methods=["GET", "POST"])
@limiter.limit(
    lambda: current_app.config.get("TWO_FACTOR_LOGIN_RATELIMIT", "10 per 10 minutes"),
    methods=["POST"],
    error_message="Zbyt wiele prob kodu 2FA. Sprobuj ponownie za chwile.",
)
def login_2fa():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard" if current_user.has_role("administrator") else "client.dashboard"))

    pending_user_id = session.get("pending_2fa_user_id")
    if not pending_user_id:
        return redirect(url_for("auth.login"))

    user = User.query.get(pending_user_id)
    if user is None or not user.two_factor_enabled or not user.two_factor_secret:
        _clear_pending_login_state()
        flash("Sesja logowania wygasla. Zaloguj sie ponownie.", "warning")
        return redirect(url_for("auth.login"))

    form = TwoFactorChallengeForm()
    if form.validate_on_submit():
        if verify_totp_code(user.two_factor_secret, form.code.data):
            remember = bool(session.get("pending_2fa_remember", False))
            next_url = _safe_next_url(session.get("pending_2fa_next"))
            _clear_pending_login_state()
            return redirect(
                _complete_login(
                    user,
                    remember=remember,
                    next_url=next_url,
                    used_two_factor=True,
                )
            )

        flash("Nieprawidlowy kod 2FA.", "danger")
        log_activity(
            "auth.login_2fa_failed",
            "user",
            "Nieudana weryfikacja 2FA",
            entity_id=user.id,
            actor=user,
            success=False,
        )
        db.session.commit()

    return render_template("auth/login_2fa.html", form=form)


@auth_bp.route("/2fa/cancel")
def cancel_login_2fa():
    _clear_pending_login_state()
    return redirect(url_for("auth.login"))


@auth_bp.route("/2fa/settings", methods=["GET", "POST"])
@login_required
def two_factor_settings():
    if not _two_factor_available():
        abort(404)

    enable_form = TwoFactorEnableForm(prefix="enable")
    disable_form = TwoFactorDisableForm(prefix="disable")

    setup_secret = session.get("two_factor_setup_secret")
    if current_user.two_factor_enabled:
        session.pop("two_factor_setup_secret", None)
        setup_secret = None
    elif not setup_secret:
        setup_secret = generate_two_factor_secret()
        session["two_factor_setup_secret"] = setup_secret

    if request.method == "POST" and enable_form.submit.data:
        if not enable_form.validate_on_submit():
            pass
        elif current_user.two_factor_enabled:
            flash("2FA jest juz wlaczone.", "info")
            return redirect(url_for("auth.two_factor_settings"))
        elif not setup_secret:
            flash("Brak sekretu konfiguracji 2FA. Odswiez strone i sproboj ponownie.", "warning")
            return redirect(url_for("auth.two_factor_settings"))
        elif verify_totp_code(setup_secret, enable_form.code.data):
            current_user.two_factor_enabled = True
            current_user.two_factor_secret = setup_secret
            session.pop("two_factor_setup_secret", None)
            log_activity(
                "auth.two_factor_enabled",
                "user",
                "Wlaczono 2FA dla konta",
                entity_id=current_user.id,
                actor=current_user,
            )
            db.session.commit()
            flash("Uwierzytelnianie dwuetapowe zostalo wlaczone.", "success")
            return redirect(url_for("auth.two_factor_settings"))
        else:
            flash("Nieprawidlowy kod 2FA. Wpisz aktualny kod z aplikacji.", "danger")

    if request.method == "POST" and disable_form.submit.data:
        if not disable_form.validate_on_submit():
            pass
        elif not current_user.two_factor_enabled or not current_user.two_factor_secret:
            flash("2FA jest juz wylaczone.", "info")
            return redirect(url_for("auth.two_factor_settings"))
        elif not current_user.check_password(disable_form.password.data):
            flash("Nieprawidlowe haslo.", "danger")
        elif not verify_totp_code(current_user.two_factor_secret, disable_form.code.data):
            flash("Nieprawidlowy kod 2FA.", "danger")
        else:
            current_user.two_factor_enabled = False
            current_user.two_factor_secret = None
            log_activity(
                "auth.two_factor_disabled",
                "user",
                "Wylaczono 2FA dla konta",
                entity_id=current_user.id,
                actor=current_user,
            )
            db.session.commit()
            flash("Uwierzytelnianie dwuetapowe zostalo wylaczone.", "success")
            return redirect(url_for("auth.two_factor_settings"))

    setup_uri = None
    setup_secret_display = None
    if setup_secret and not current_user.two_factor_enabled:
        issuer = (current_app.config.get("TWO_FACTOR_ISSUER") or current_app.config.get("APP_NAME") or "Hosting Panel").strip()
        username = current_user.email or current_user.username
        setup_uri = build_totp_uri(secret=setup_secret, username=username, issuer=issuer)
        setup_secret_display = format_secret_for_display(setup_secret)

    return render_template(
        "auth/two_factor_settings.html",
        enable_form=enable_form,
        disable_form=disable_form,
        setup_uri=setup_uri,
        setup_secret_display=setup_secret_display,
        title="Ustawienia 2FA",
    )


@auth_bp.route("/logout")
@login_required
def logout():
    user_id = current_user.id
    log_activity("auth.logout", "user", "Wylogowanie użytkownika", entity_id=user_id, actor=current_user)
    db.session.commit()
    _clear_pending_login_state()
    session.pop("two_factor_setup_secret", None)
    logout_user()
    flash("Wylogowano.", "info")
    return redirect(url_for("auth.login"))
