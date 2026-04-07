from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from flask import current_app

from panel.extensions import db
from panel.models import Domain, DomainRegistration, User
from panel.services.audit import log_activity


DOMAIN_NAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)


class RegistrarError(RuntimeError):
    pass


def _provider_name() -> str:
    return str(current_app.config.get("DOMAIN_REGISTRAR_PROVIDER", "mock") or "mock").strip().lower() or "mock"


def _normalize_years(years: int | str | None) -> int:
    try:
        parsed = int(years)
    except (TypeError, ValueError):
        parsed = 1
    return max(1, min(parsed, 10))


def _normalize_name_servers(name_servers: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if name_servers is None:
        configured = str(current_app.config.get("DOMAIN_REGISTRAR_DEFAULT_NAMESERVERS", "") or "")
        values = configured.split(",")
    elif isinstance(name_servers, str):
        values = [line.strip() for line in name_servers.splitlines()]
    else:
        values = [str(item).strip() for item in name_servers]

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = value.strip().lower()
        if not item:
            continue
        if DOMAIN_NAME_RE.fullmatch(item) is None:
            continue
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _ensure_supported_provider(provider: str) -> None:
    if provider != "mock":
        raise RegistrarError(f"Nieobslugiwany provider registrar: {provider}")


def register_domain_with_registrar(
    domain: Domain,
    *,
    years: int | str | None = 1,
    auto_renew: bool = True,
    name_servers: list[str] | tuple[str, ...] | str | None = None,
    actor: User | None = None,
) -> DomainRegistration:
    provider = _provider_name()
    _ensure_supported_provider(provider)

    if DOMAIN_NAME_RE.fullmatch((domain.name or "").strip().lower()) is None:
        raise RegistrarError("Nieprawidlowa nazwa domeny do rejestracji.")

    registration = domain.registration
    if registration is not None and registration.status in {"active", "pending_transfer"}:
        raise RegistrarError("Domena ma juz aktywna rejestracje registrar.")

    years_normalized = _normalize_years(years)
    today = date.today()
    expires_on = today + timedelta(days=365 * years_normalized)
    servers = _normalize_name_servers(name_servers)

    if registration is None:
        registration = DomainRegistration(domain=domain, client=domain.client)
        db.session.add(registration)

    registration.registrar = provider
    registration.external_registration_id = registration.external_registration_id or f"mock-{domain.id}-{int(datetime.utcnow().timestamp())}"
    registration.status = "active"
    registration.registered_on = today
    registration.expires_on = expires_on
    registration.auto_renew = bool(auto_renew)
    registration.name_servers_json = servers
    registration.last_synced_at = datetime.utcnow()
    registration.last_sync_status = "ok"
    registration.last_sync_message = "Rejestracja mock zostala wykonana."

    metadata = dict(registration.metadata_json or {})
    metadata["last_registration"] = {
        "provider": provider,
        "years": years_normalized,
        "registered_at": datetime.utcnow().isoformat(),
        "auto_renew": bool(auto_renew),
    }
    registration.metadata_json = metadata

    log_activity(
        "domains.registrar_register",
        "domain_registration",
        f"Zarejestrowano domene {domain.name} u providera {provider}",
        entity_id=domain.id,
        client=domain.client,
        actor=actor,
        metadata={
            "provider": provider,
            "domain": domain.name,
            "expires_on": expires_on.isoformat(),
            "name_servers": servers,
        },
    )
    return registration


def renew_domain_registration(
    registration: DomainRegistration,
    *,
    years: int | str | None = 1,
    actor: User | None = None,
) -> DomainRegistration:
    provider = (registration.registrar or "mock").strip().lower() or "mock"
    _ensure_supported_provider(provider)

    years_normalized = _normalize_years(years)
    today = date.today()
    base_date = registration.expires_on if registration.expires_on and registration.expires_on > today else today
    registration.expires_on = base_date + timedelta(days=365 * years_normalized)
    registration.status = "active"
    registration.last_synced_at = datetime.utcnow()
    registration.last_sync_status = "ok"
    registration.last_sync_message = f"Odnowiono o {years_normalized} rok/lata."

    metadata = dict(registration.metadata_json or {})
    renewals = list(metadata.get("renewals", []))
    renewals.append({"years": years_normalized, "renewed_at": datetime.utcnow().isoformat()})
    metadata["renewals"] = renewals[-20:]
    registration.metadata_json = metadata

    log_activity(
        "domains.registrar_renew",
        "domain_registration",
        f"Odnowiono domene {registration.domain.name}",
        entity_id=registration.domain_id,
        client=registration.client,
        actor=actor,
        metadata={
            "provider": provider,
            "years": years_normalized,
            "expires_on": registration.expires_on.isoformat() if registration.expires_on else None,
        },
    )
    return registration


def sync_domain_registration(registration: DomainRegistration, *, actor: User | None = None) -> DomainRegistration:
    provider = (registration.registrar or "mock").strip().lower() or "mock"
    _ensure_supported_provider(provider)

    today = date.today()
    registration.last_synced_at = datetime.utcnow()
    if registration.expires_on and registration.expires_on < today:
        registration.status = "expired"
        registration.last_sync_status = "expired"
        registration.last_sync_message = "Domena wygasla wg lokalnego terminu."
    else:
        registration.status = "active"
        registration.last_sync_status = "ok"
        registration.last_sync_message = "Synchronizacja zakonczona pomyslnie."

    log_activity(
        "domains.registrar_sync",
        "domain_registration",
        f"Zsynchronizowano status registrar dla domeny {registration.domain.name}",
        entity_id=registration.domain_id,
        client=registration.client,
        actor=actor,
        metadata={
            "provider": provider,
            "status": registration.status,
            "expires_on": registration.expires_on.isoformat() if registration.expires_on else None,
        },
    )
    return registration
