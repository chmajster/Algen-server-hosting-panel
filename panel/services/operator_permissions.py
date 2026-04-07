from __future__ import annotations

from dataclasses import dataclass

from flask import request

from panel.extensions import db
from panel.models import OperatorPermission, User


@dataclass(frozen=True)
class OperatorDomain:
    key: str
    label: str


OPERATOR_DOMAINS: tuple[OperatorDomain, ...] = (
    OperatorDomain("admin", "Panel admin / ustawienia"),
    OperatorDomain("billing", "Billing"),
    OperatorDomain("domains", "Domeny"),
    OperatorDomain("databases", "Bazy danych"),
    OperatorDomain("dns", "DNS"),
    OperatorDomain("ftp", "FTP"),
    OperatorDomain("mail", "Poczta"),
    OperatorDomain("ssl", "SSL"),
    OperatorDomain("backups", "Backupy"),
    OperatorDomain("monitoring", "Monitoring"),
    OperatorDomain("status", "Status page"),
    OperatorDomain("tickets", "Tickety"),
    OperatorDomain("webhooks", "Webhooki"),
    OperatorDomain("hosts", "Hosts"),
    OperatorDomain("files", "Pliki"),
)

DOMAIN_BY_BLUEPRINT: dict[str, str] = {
    "admin": "admin",
    "billing": "billing",
    "domains": "domains",
    "databases": "databases",
    "dns": "dns",
    "ftp": "ftp",
    "mail": "mail",
    "ssl": "ssl",
    "backups": "backups",
    "monitoring": "monitoring",
    "status": "status",
    "tickets": "tickets",
    "webhooks": "webhooks",
    "hosts": "hosts",
    "files": "files",
}


def domain_choices() -> tuple[OperatorDomain, ...]:
    return OPERATOR_DOMAINS


def _operator_rows(user: User) -> list[OperatorPermission]:
    return OperatorPermission.query.filter_by(user_id=user.id).all()


def has_custom_permissions(user: User) -> bool:
    return bool(_operator_rows(user))


def permissions_matrix(user: User) -> dict[str, dict[str, bool]]:
    rows = _operator_rows(user)
    row_map = {row.domain: row for row in rows}
    custom_enabled = bool(rows)

    matrix: dict[str, dict[str, bool]] = {}
    for domain in OPERATOR_DOMAINS:
        row = row_map.get(domain.key)
        if row is None:
            matrix[domain.key] = {"can_read": not custom_enabled, "can_write": not custom_enabled}
            continue
        matrix[domain.key] = {"can_read": bool(row.can_read), "can_write": bool(row.can_write)}
    return matrix


def save_permissions_matrix(*, user: User, enabled: bool, matrix: dict[str, dict[str, bool]]) -> None:
    OperatorPermission.query.filter_by(user_id=user.id).delete(synchronize_session=False)
    if not enabled:
        return

    for domain in OPERATOR_DOMAINS:
        flags = matrix.get(domain.key, {})
        can_write = bool(flags.get("can_write", False))
        can_read = bool(flags.get("can_read", False)) or can_write
        db.session.add(
            OperatorPermission(
                user_id=user.id,
                domain=domain.key,
                can_read=can_read,
                can_write=can_write,
            )
        )


def _resolve_domain_from_request() -> str | None:
    blueprint = request.blueprint or ""
    return DOMAIN_BY_BLUEPRINT.get(blueprint)


def _requires_write_access() -> bool:
    return request.method not in {"GET", "HEAD", "OPTIONS"}


def can_operator_access_request(user: User) -> bool:
    if not user.has_role("operator"):
        return True

    rows = _operator_rows(user)
    if not rows:
        # Legacy fallback: operators without explicit matrix keep full access.
        return True

    domain = _resolve_domain_from_request()
    if not domain:
        return True

    permission = next((row for row in rows if row.domain == domain), None)
    if permission is None:
        return False

    if _requires_write_access():
        return bool(permission.can_write)
    return bool(permission.can_read or permission.can_write)
