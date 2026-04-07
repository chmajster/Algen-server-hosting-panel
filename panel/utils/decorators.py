from __future__ import annotations

from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user


def _expanded_roles(roles: tuple[str, ...]) -> set[str]:
    allowed = set(roles)
    # Operator shares administrator panel responsibilities.
    if "administrator" in allowed:
        allowed.add("operator")
    return allowed


def roles_required(*roles: str):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.url))
            if current_user.role is None or current_user.role.name not in _expanded_roles(roles):
                abort(403)
            return func(*args, **kwargs)

        return wrapper

    return decorator


def active_account_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if getattr(current_user, "status", None) not in {"active", "overdue", "suspended_financial"}:
            flash("Konto nie jest aktywne.", "danger")
            return redirect(url_for("auth.logout"))
        return func(*args, **kwargs)

    return wrapper
