from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from panel.extensions import db
from panel.models import BulkOperation, BulkOperationItem, Client, ClientService, ServicePlan, User
from panel.services.billing import plan_price_for_period, update_client_financial_status
from panel.services.client_apache import ClientApacheServiceError, sync_client_apache_instance


def create_bulk_operation(
    *,
    operation_type: str,
    target_type: str,
    initiated_by: User | None,
    dry_run: bool,
    requested_filters: dict | None,
) -> BulkOperation:
    operation = BulkOperation(
        operation_type=operation_type,
        target_type=target_type,
        initiated_by=initiated_by,
        dry_run=bool(dry_run),
        status="running",
        requested_filters_json=requested_filters or {},
        started_at=datetime.utcnow(),
    )
    db.session.add(operation)
    db.session.flush()
    return operation


def add_bulk_item(
    operation: BulkOperation,
    *,
    entity_type: str,
    entity_id: str | int,
    success: bool,
    message: str,
    metadata: dict | None = None,
) -> None:
    db.session.add(
        BulkOperationItem(
            operation=operation,
            entity_type=entity_type,
            entity_id=str(entity_id),
            success=bool(success),
            message=message[:500],
            metadata_json=metadata or {},
        )
    )


def finalize_bulk_operation(operation: BulkOperation) -> dict:
    items = list(operation.items)
    success_count = sum(1 for item in items if item.success)
    failed_count = len(items) - success_count
    summary = {
        "total": len(items),
        "success": success_count,
        "failed": failed_count,
        "dry_run": bool(operation.dry_run),
    }
    operation.result_summary_json = summary
    operation.status = "completed" if failed_count == 0 else "partial_failed"
    operation.completed_at = datetime.utcnow()
    return summary


def bulk_change_service_plan(
    *,
    service_ids: list[int],
    target_plan: ServicePlan,
    actor: User | None,
    dry_run: bool,
) -> tuple[BulkOperation, dict]:
    operation = create_bulk_operation(
        operation_type="service_plan_change",
        target_type="client_service",
        initiated_by=actor,
        dry_run=dry_run,
        requested_filters={"service_ids": service_ids, "target_plan_id": target_plan.id},
    )

    services = ClientService.query.filter(ClientService.id.in_(service_ids)).all() if service_ids else []
    service_map = {service.id: service for service in services}

    for service_id in service_ids:
        service = service_map.get(service_id)
        if service is None:
            add_bulk_item(
                operation,
                entity_type="client_service",
                entity_id=service_id,
                success=False,
                message="Nie znaleziono uslugi.",
            )
            continue

        if service.service_type != "hosting":
            add_bulk_item(
                operation,
                entity_type="client_service",
                entity_id=service.id,
                success=False,
                message="Zmiana planu bulk obsluguje tylko uslugi hostingowe.",
            )
            continue

        if service.service_plan_id == target_plan.id:
            add_bulk_item(
                operation,
                entity_type="client_service",
                entity_id=service.id,
                success=True,
                message="Plan jest juz przypisany.",
            )
            continue

        new_amount = Decimal(str(plan_price_for_period(target_plan, service.billing_period)))
        if not dry_run:
            service.service_plan_id = target_plan.id
            service.recurring_amount = new_amount
            db.session.add(service)

        add_bulk_item(
            operation,
            entity_type="client_service",
            entity_id=service.id,
            success=True,
            message="Plan zmieniony." if not dry_run else "Podglad: plan zostanie zmieniony.",
            metadata={"new_plan_id": target_plan.id, "new_amount": str(new_amount)},
        )

    return operation, finalize_bulk_operation(operation)


def bulk_update_client_limits(
    *,
    client_ids: list[int],
    disk_hard_mb: int | None,
    inode_limit: int | None,
    actor: User | None,
    dry_run: bool,
) -> tuple[BulkOperation, dict]:
    operation = create_bulk_operation(
        operation_type="client_limits_update",
        target_type="client",
        initiated_by=actor,
        dry_run=dry_run,
        requested_filters={"client_ids": client_ids, "disk_hard_mb": disk_hard_mb, "inode_limit": inode_limit},
    )

    clients = Client.query.filter(Client.id.in_(client_ids)).all() if client_ids else []
    client_map = {client.id: client for client in clients}

    for client_id in client_ids:
        client = client_map.get(client_id)
        if client is None:
            add_bulk_item(operation, entity_type="client", entity_id=client_id, success=False, message="Nie znaleziono klienta.")
            continue

        limits = dict(client.resource_limits or {})
        new_limits = dict(limits)
        if disk_hard_mb is not None:
            new_limits["disk_hard_mb"] = int(disk_hard_mb)
        if inode_limit is not None:
            new_limits["inode_limit"] = int(inode_limit)

        if not dry_run:
            client.resource_limits = new_limits
            db.session.add(client)

        add_bulk_item(
            operation,
            entity_type="client",
            entity_id=client.id,
            success=True,
            message="Limity klienta zaktualizowane." if not dry_run else "Podglad: limity klienta zostana zaktualizowane.",
            metadata={"limits": new_limits},
        )

    return operation, finalize_bulk_operation(operation)


def bulk_lock_user_accounts(
    *,
    user_ids: list[int],
    reason: str,
    lock: bool,
    actor: User | None,
    dry_run: bool,
) -> tuple[BulkOperation, dict]:
    operation = create_bulk_operation(
        operation_type="user_lock" if lock else "user_unlock",
        target_type="user",
        initiated_by=actor,
        dry_run=dry_run,
        requested_filters={"user_ids": user_ids, "reason": reason, "lock": lock},
    )

    users = User.query.filter(User.id.in_(user_ids)).all() if user_ids else []
    user_map = {user.id: user for user in users}

    for user_id in user_ids:
        user = user_map.get(user_id)
        if user is None:
            add_bulk_item(operation, entity_type="user", entity_id=user_id, success=False, message="Nie znaleziono uzytkownika.")
            continue

        if user.has_role("administrator") and lock:
            add_bulk_item(
                operation,
                entity_type="user",
                entity_id=user.id,
                success=False,
                message="Nie mozna masowo blokowac kont administratorow.",
            )
            continue

        if not dry_run:
            if lock:
                user.status = "inactive"
                user.is_active_account = False
                user.manual_lock_reason = reason[:255]
            else:
                user.status = "active"
                user.is_active_account = True
                user.manual_lock_reason = None
            db.session.add(user)

        add_bulk_item(
            operation,
            entity_type="user",
            entity_id=user.id,
            success=True,
            message=("Konto zostanie zablokowane." if lock else "Konto zostanie odblokowane.")
            if dry_run
            else ("Konto zablokowane." if lock else "Konto odblokowane."),
            metadata={"lock": lock},
        )

    return operation, finalize_bulk_operation(operation)


def bulk_restart_hosting_services(
    *,
    service_ids: list[int],
    actor: User | None,
    dry_run: bool,
) -> tuple[BulkOperation, dict]:
    operation = create_bulk_operation(
        operation_type="service_restart",
        target_type="client_service",
        initiated_by=actor,
        dry_run=dry_run,
        requested_filters={"service_ids": service_ids},
    )

    services = ClientService.query.filter(ClientService.id.in_(service_ids)).all() if service_ids else []
    service_map = {service.id: service for service in services}

    for service_id in service_ids:
        service = service_map.get(service_id)
        if service is None:
            add_bulk_item(operation, entity_type="client_service", entity_id=service_id, success=False, message="Nie znaleziono uslugi.")
            continue

        if service.service_type != "hosting":
            add_bulk_item(
                operation,
                entity_type="client_service",
                entity_id=service.id,
                success=False,
                message="Restart bulk obsluguje tylko uslugi hostingowe.",
            )
            continue

        if dry_run:
            add_bulk_item(
                operation,
                entity_type="client_service",
                entity_id=service.id,
                success=True,
                message="Podglad: usluga zostanie zrestartowana.",
            )
            continue

        try:
            provisioning = sync_client_apache_instance(service.client, reason="bulk_service_restart", actor=actor)
            add_bulk_item(
                operation,
                entity_type="client_service",
                entity_id=service.id,
                success=True,
                message="Usluga zrestartowana.",
                metadata={"provisioning": provisioning},
            )
        except ClientApacheServiceError as exc:
            add_bulk_item(
                operation,
                entity_type="client_service",
                entity_id=service.id,
                success=False,
                message=f"Restart nieudany: {exc}",
            )

    return operation, finalize_bulk_operation(operation)


def bulk_set_service_manual_state(
    *,
    service_ids: list[int],
    suspend: bool,
    reason: str,
    actor: User | None,
    dry_run: bool,
) -> tuple[BulkOperation, dict]:
    operation = create_bulk_operation(
        operation_type="service_manual_suspend" if suspend else "service_manual_unsuspend",
        target_type="client_service",
        initiated_by=actor,
        dry_run=dry_run,
        requested_filters={"service_ids": service_ids, "suspend": suspend, "reason": reason},
    )

    services = ClientService.query.filter(ClientService.id.in_(service_ids)).all() if service_ids else []
    service_map = {service.id: service for service in services}

    for service_id in service_ids:
        service = service_map.get(service_id)
        if service is None:
            add_bulk_item(operation, entity_type="client_service", entity_id=service_id, success=False, message="Nie znaleziono uslugi.")
            continue

        if suspend and service.status == "blocked_manual":
            add_bulk_item(operation, entity_type="client_service", entity_id=service.id, success=True, message="Usluga juz recznie zawieszona.")
            continue
        if not suspend and service.status != "blocked_manual":
            add_bulk_item(operation, entity_type="client_service", entity_id=service.id, success=True, message="Usluga nie jest recznie zawieszona.")
            continue

        if not dry_run:
            if suspend:
                service.status = "blocked_manual"
                service.manual_lock_reason = reason[:255]
            else:
                service.status = "active"
                service.manual_lock_reason = None
            db.session.add(service)
            update_client_financial_status(service.client, actor=actor)

        add_bulk_item(
            operation,
            entity_type="client_service",
            entity_id=service.id,
            success=True,
            message=("Podglad: usluga bedzie zawieszona." if suspend else "Podglad: usluga bedzie odwieszona.")
            if dry_run
            else ("Usluga recznie zawieszona." if suspend else "Usluga recznie odwieszona."),
            metadata={"suspend": suspend},
        )

    return operation, finalize_bulk_operation(operation)
