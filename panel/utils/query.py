from __future__ import annotations

from flask import abort
from flask_login import current_user

from panel.models import (
    Backup,
    Client,
    ClientService,
    DNSZone,
    Domain,
    FTPAccount,
    HostingDatabase,
    Mailbox,
    Role,
    ServicePlan,
    SSLCertificate,
)


def client_choices() -> list[tuple[int, str]]:
    return [(client.id, client.user.username) for client in Client.query.order_by(Client.id.asc()).all()]


def service_plan_choices() -> list[tuple[int, str]]:
    plans = [(0, "Brak planu")]
    plans.extend((plan.id, plan.name) for plan in ServicePlan.query.order_by(ServicePlan.name.asc()).all())
    return plans


def service_choices(client_id: int | None = None) -> list[tuple[int, str]]:
    query = ClientService.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    choices = [(0, "Brak usługi")]
    choices.extend((service.id, service.name) for service in query.order_by(ClientService.name.asc()).all())
    return choices


def domain_choices(client_id: int | None = None) -> list[tuple[int, str]]:
    query = Domain.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    return [(domain.id, domain.name) for domain in query.order_by(Domain.name.asc()).all()]


def zone_choices(client_id: int | None = None) -> list[tuple[int, str]]:
    query = DNSZone.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    return [(zone.id, zone.name) for zone in query.order_by(DNSZone.name.asc()).all()]


def database_choices(client_id: int | None = None) -> list[tuple[int, str]]:
    query = HostingDatabase.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    return [(item.id, item.name) for item in query.order_by(HostingDatabase.name.asc()).all()]


def mailbox_choices(client_id: int | None = None) -> list[tuple[int, str]]:
    query = Mailbox.query
    if client_id:
        query = query.filter_by(client_id=client_id)
    return [(item.id, item.email) for item in query.order_by(Mailbox.email.asc()).all()]


def optionalized(choices: list[tuple[int, str]], label: str = "Brak") -> list[tuple[int, str]]:
    return [(0, label)] + choices


def current_client() -> Client:
    if not current_user.is_authenticated or current_user.client_profile is None:
        abort(403)
    return current_user.client_profile


def owned_or_404(model, obj_id: int):
    obj = model.query.get_or_404(obj_id)
    client = current_client()
    owner_id = getattr(obj, "client_id", None)
    if owner_id is None and hasattr(obj, "domain"):
        owner_id = obj.domain.client_id
    if owner_id is None and hasattr(obj, "database"):
        owner_id = obj.database.client_id
    if owner_id is None and hasattr(obj, "mailbox"):
        owner_id = obj.mailbox.client_id
    if owner_id != client.id:
        abort(404)
    return obj
