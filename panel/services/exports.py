from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from io import BytesIO, StringIO

from panel.extensions import db
from panel.models import (
    BillingCycle,
    Client,
    ClientResourceSample,
    ClientService,
    ComplianceChecklistItem,
    ComplianceResult,
    DisasterRecoveryCheckRun,
    EventStreamEntry,
    ExportJob,
    Ticket,
)


def _normalize_cell(value):
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return str(value)


def serialize_csv(headers: list[str], rows: list[list]) -> BytesIO:
    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_normalize_cell(value) for value in row])
    payload = BytesIO(stream.getvalue().encode("utf-8"))
    payload.seek(0)
    return payload


def serialize_xlsx(headers: list[str], rows: list[list]) -> BytesIO:
    try:
        from openpyxl import Workbook
    except Exception as exc:
        raise RuntimeError("Biblioteka openpyxl nie jest dostepna.") from exc

    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "export"
    worksheet.append(headers)
    for row in rows:
        worksheet.append([_normalize_cell(value) for value in row])

    payload = BytesIO()
    workbook.save(payload)
    payload.seek(0)
    return payload


def create_export_job(
    *,
    dataset: str,
    format_name: str,
    requested_by,
    filters: dict,
    row_count: int,
    status: str = "completed",
    error_message: str | None = None,
) -> ExportJob:
    job = ExportJob(
        dataset=dataset,
        format=format_name,
        requested_by=requested_by,
        filters_json=filters,
        status=status,
        row_count=max(0, int(row_count or 0)),
        error_message=(error_message or "")[:500] or None,
        completed_at=datetime.utcnow(),
        metadata_json={"generated_at": datetime.utcnow().isoformat()},
    )
    db.session.add(job)
    return job


def dataset_clients(*, status_filter: str | None = None, limit: int = 5000) -> tuple[list[str], list[list]]:
    query = Client.query
    if status_filter:
        query = query.filter_by(billing_status=status_filter)

    clients = query.order_by(Client.created_at.desc()).limit(max(1, limit)).all()
    headers = [
        "client_id",
        "username",
        "full_name",
        "email",
        "company_name",
        "billing_status",
        "balance",
        "currency",
        "created_at",
    ]
    rows: list[list] = []
    for client in clients:
        user = client.user
        balance = client.balance.balance if client.balance else None
        currency = client.balance.currency if client.balance else None
        rows.append(
            [
                client.id,
                user.username if user else "",
                user.full_name if user else "",
                user.email if user else "",
                client.company_name or "",
                client.billing_status,
                balance,
                currency,
                client.created_at,
            ]
        )
    return headers, rows


def dataset_invoices(
    *,
    status_filter: str | None = None,
    client_id: int | None = None,
    limit: int = 5000,
) -> tuple[list[str], list[list]]:
    query = BillingCycle.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    if client_id:
        query = query.join(BillingCycle.client_service).filter(ClientService.client_id == client_id)

    cycles = query.order_by(BillingCycle.due_date.desc(), BillingCycle.id.desc()).limit(max(1, limit)).all()
    headers = [
        "invoice_number",
        "cycle_id",
        "client",
        "service_name",
        "billing_period",
        "amount",
        "due_date",
        "status",
        "last_charged_at",
    ]
    rows: list[list] = []
    for cycle in cycles:
        service = cycle.client_service
        client_user = service.client.user if service and service.client else None
        rows.append(
            [
                f"INV-{cycle.id:06d}",
                cycle.id,
                client_user.username if client_user else "",
                service.name if service else "",
                cycle.cycle_type,
                cycle.amount,
                cycle.due_date,
                cycle.status,
                cycle.last_charged_at,
            ]
        )
    return headers, rows


def dataset_tickets(
    *,
    status_filter: str | None = None,
    category_filter: str | None = None,
    limit: int = 5000,
) -> tuple[list[str], list[list]]:
    query = Ticket.query
    if status_filter:
        query = query.filter_by(status=status_filter)
    if category_filter:
        query = query.filter_by(category=category_filter)

    tickets = query.order_by(Ticket.updated_at.desc()).limit(max(1, limit)).all()
    headers = [
        "ticket_number",
        "ticket_id",
        "client",
        "subject",
        "category",
        "priority",
        "status",
        "assigned_to",
        "last_message_at",
        "created_at",
    ]
    rows: list[list] = []
    for ticket in tickets:
        rows.append(
            [
                ticket.display_number,
                ticket.id,
                ticket.client.user.username if ticket.client and ticket.client.user else "",
                ticket.subject,
                ticket.category,
                ticket.priority,
                ticket.status,
                ticket.assigned_to.username if ticket.assigned_to else "",
                ticket.last_message_at,
                ticket.created_at,
            ]
        )
    return headers, rows


def dataset_resource_usage(*, client_id: int | None = None, limit: int = 5000) -> tuple[list[str], list[list]]:
    query = ClientResourceSample.query
    if client_id:
        query = query.filter_by(client_id=client_id)

    samples = query.order_by(ClientResourceSample.created_at.desc()).limit(max(1, limit)).all()
    headers = [
        "sample_id",
        "client_id",
        "client",
        "cpu_percent",
        "memory_mb",
        "memory_limit_mb",
        "disk_mb",
        "inode_count",
        "database_count",
        "mailbox_count",
        "created_at",
    ]
    rows: list[list] = []
    for sample in samples:
        rows.append(
            [
                sample.id,
                sample.client_id,
                sample.client.user.username if sample.client and sample.client.user else "",
                sample.cpu_percent,
                sample.memory_mb,
                sample.memory_limit_mb,
                sample.disk_mb,
                sample.inode_count,
                sample.database_count,
                sample.mailbox_count,
                sample.created_at,
            ]
        )
    return headers, rows


def dataset_compliance(*, client_id: int | None = None, limit: int = 5000) -> tuple[list[str], list[list]]:
    query = ComplianceResult.query
    if client_id:
        query = query.filter_by(client_id=client_id)

    rows_raw = query.order_by(ComplianceResult.created_at.desc()).limit(max(1, limit)).all()
    headers = [
        "result_id",
        "run_id",
        "client_id",
        "client",
        "check_code",
        "status",
        "severity",
        "score",
        "message",
        "evidence_ref",
        "created_at",
    ]
    rows: list[list] = []
    for item in rows_raw:
        rows.append(
            [
                item.id,
                item.run_id,
                item.client_id,
                item.client.user.username if item.client and item.client.user else "",
                item.check_code,
                item.status,
                item.severity,
                item.score,
                item.message,
                item.evidence_ref,
                item.created_at,
            ]
        )
    return headers, rows


def dataset_compliance_controls(*, client_id: int | None = None, limit: int = 5000) -> tuple[list[str], list[list]]:
    query = ComplianceChecklistItem.query
    if client_id:
        query = query.filter_by(client_id=client_id)

    rows_raw = query.order_by(ComplianceChecklistItem.updated_at.desc(), ComplianceChecklistItem.id.desc()).limit(max(1, limit)).all()
    headers = [
        "control_id",
        "client_id",
        "client",
        "control_code",
        "title",
        "status",
        "owner",
        "due_date",
        "evidence_count",
        "updated_at",
    ]
    rows: list[list] = []
    for item in rows_raw:
        rows.append(
            [
                item.id,
                item.client_id,
                item.client.user.username if item.client and item.client.user else "",
                item.control_code,
                item.title,
                item.status,
                item.owner.username if item.owner else "",
                item.due_date,
                len(item.evidence_links),
                item.updated_at,
            ]
        )
    return headers, rows


def dataset_dr_readiness(*, client_id: int | None = None, limit: int = 5000) -> tuple[list[str], list[list]]:
    query = DisasterRecoveryCheckRun.query
    if client_id:
        query = query.filter_by(client_id=client_id)

    rows_raw = query.order_by(DisasterRecoveryCheckRun.checked_at.desc(), DisasterRecoveryCheckRun.id.desc()).limit(max(1, limit)).all()
    headers = [
        "run_id",
        "client_id",
        "client",
        "status",
        "score",
        "rpo_minutes",
        "rto_minutes",
        "replication_status",
        "last_sync_at",
        "run_type",
        "checked_at",
        "message",
    ]
    rows: list[list] = []
    for item in rows_raw:
        details_json = dict(item.details_json or {})
        rows.append(
            [
                item.id,
                item.client_id,
                item.client.user.username if item.client and item.client.user else "",
                item.status,
                item.score,
                item.rpo_minutes,
                item.rto_minutes,
                details_json.get("replication_status"),
                details_json.get("last_sync_at"),
                details_json.get("run_type", "readiness_check"),
                item.checked_at,
                item.message,
            ]
        )
    return headers, rows


def dataset_events(*, client_id: int | None = None, limit: int = 5000) -> tuple[list[str], list[list]]:
    query = EventStreamEntry.query
    if client_id:
        query = query.filter_by(client_id=client_id)

    rows_raw = query.order_by(EventStreamEntry.event_at.desc(), EventStreamEntry.id.desc()).limit(max(1, limit)).all()
    headers = [
        "event_id",
        "event_type",
        "category",
        "severity",
        "source",
        "message",
        "client_id",
        "client",
        "actor_user_id",
        "event_at",
        "created_at",
    ]
    rows: list[list] = []
    for item in rows_raw:
        rows.append(
            [
                item.id,
                item.event_type,
                item.category,
                item.severity,
                item.source,
                item.message,
                item.client_id,
                item.client.user.username if item.client and item.client.user else "",
                item.actor_user_id,
                item.event_at,
                item.created_at,
            ]
        )
    return headers, rows
