from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from flask import current_app

from panel.extensions import db
from panel.models import ApprovalDecision, ApprovalRequest, Backup, Domain, User
from panel.services.audit import log_activity
from panel.services.backup_restore import create_restore_job, process_restore_job
from panel.services.client_apache import ClientApacheServiceError, sync_client_apache_instance


class ApprovalError(RuntimeError):
    pass


class ApprovalDecisionError(ApprovalError):
    pass


class ApprovalExecutionError(ApprovalError):
    pass


_EXECUTOR = Callable[[ApprovalRequest, User | None], dict[str, Any]]
_ROLE_RANK = {"client": 0, "operator": 1, "administrator": 2}


def _config_bool(key: str, default: bool) -> bool:
    value = current_app.config.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _config_int(key: str, default: int) -> int:
    try:
        return int(current_app.config.get(key, default))
    except (TypeError, ValueError):
        return default


def _split_csv(value: str | None) -> list[str]:
    return [item.strip().lower() for item in (value or "").split(",") if item.strip()]


def _required_counts_map() -> dict[str, int]:
    raw = current_app.config.get("APPROVALS_REQUIRED_COUNTS", "")
    mapping: dict[str, int] = {}
    for part in str(raw or "").split(","):
        chunk = part.strip()
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        action_key = key.strip().lower()
        if not action_key:
            continue
        try:
            mapping[action_key] = max(1, int(value.strip()))
        except ValueError:
            continue
    return mapping


def approvals_enabled() -> bool:
    return _config_bool("APPROVALS_ENABLED", True)


def risky_actions() -> set[str]:
    return set(_split_csv(current_app.config.get("APPROVALS_RISKY_ACTIONS", "")))


def required_approvals_for(action_key: str) -> int:
    normalized = (action_key or "").strip().lower()
    fallback = max(1, _config_int("APPROVALS_DEFAULT_REQUIRED", 1))
    return _required_counts_map().get(normalized, fallback)


def action_requires_approval(action_key: str) -> bool:
    normalized = (action_key or "").strip().lower()
    return approvals_enabled() and normalized in risky_actions() and required_approvals_for(normalized) > 0


def _min_approver_role() -> str:
    role = str(current_app.config.get("APPROVALS_MIN_APPROVER_ROLE", "operator") or "operator").strip().lower()
    return role if role in _ROLE_RANK else "operator"


def _allow_self_approval() -> bool:
    return _config_bool("APPROVALS_ALLOW_SELF_APPROVAL", False)


def _expires_at() -> datetime | None:
    ttl_minutes = _config_int("APPROVALS_REQUEST_TTL_MINUTES", 1440)
    if ttl_minutes <= 0:
        return None
    return datetime.utcnow() + timedelta(minutes=ttl_minutes)


def expire_due_approval_requests(*, limit: int = 200) -> int:
    now = datetime.utcnow()
    rows = (
        ApprovalRequest.query.filter_by(status="pending")
        .filter(ApprovalRequest.expires_at.is_not(None))
        .filter(ApprovalRequest.expires_at <= now)
        .order_by(ApprovalRequest.expires_at.asc())
        .limit(max(1, limit))
        .all()
    )
    changed = 0
    for row in rows:
        row.status = "expired"
        changed += 1
    return changed


def _active_pending_request(*, action_key: str, target_type: str, target_id: str) -> ApprovalRequest | None:
    now = datetime.utcnow()
    row = (
        ApprovalRequest.query.filter_by(
            action_key=action_key,
            target_type=target_type,
            target_id=target_id,
            status="pending",
        )
        .order_by(ApprovalRequest.created_at.desc())
        .first()
    )
    if row is None:
        return None
    if row.expires_at is not None and row.expires_at <= now:
        row.status = "expired"
        return None
    return row


def create_approval_request(
    *,
    action_key: str,
    target_type: str,
    target_id: str | int,
    requested_by: User,
    reason: str | None = None,
    client=None,
    metadata: dict[str, Any] | None = None,
) -> tuple[ApprovalRequest, bool]:
    normalized_action = (action_key or "").strip().lower()
    normalized_target_type = (target_type or "").strip().lower()
    normalized_target_id = str(target_id)

    existing = _active_pending_request(
        action_key=normalized_action,
        target_type=normalized_target_type,
        target_id=normalized_target_id,
    )
    if existing is not None:
        return existing, False

    request_obj = ApprovalRequest(
        action_key=normalized_action,
        target_type=normalized_target_type,
        target_id=normalized_target_id,
        client=client,
        requested_by=requested_by,
        status="pending",
        required_approvals=required_approvals_for(normalized_action),
        min_approver_role=_min_approver_role(),
        reason=(reason or "").strip()[:255] or None,
        expires_at=_expires_at(),
        metadata_json=metadata or {},
    )
    db.session.add(request_obj)
    db.session.flush()

    log_activity(
        "approvals.request_created",
        "approval_request",
        f"Utworzono wniosek akceptacji #{request_obj.id} dla akcji {normalized_action}",
        entity_id=request_obj.id,
        client=client,
        actor=requested_by,
        metadata={
            "action_key": normalized_action,
            "target_type": normalized_target_type,
            "target_id": normalized_target_id,
            "required_approvals": request_obj.required_approvals,
            "expires_at": request_obj.expires_at.isoformat() if request_obj.expires_at else None,
        },
    )
    return request_obj, True


def _role_allows_decision(request_obj: ApprovalRequest, user: User) -> bool:
    user_rank = _ROLE_RANK.get(user.role.name if user.role else "", -1)
    min_rank = _ROLE_RANK.get(request_obj.min_approver_role, _ROLE_RANK["operator"])
    return user_rank >= min_rank


def _count_approvals(request_obj: ApprovalRequest) -> int:
    return sum(1 for row in request_obj.decisions if row.decision == "approve")


def _count_rejections(request_obj: ApprovalRequest) -> int:
    return sum(1 for row in request_obj.decisions if row.decision == "reject")


def _update_request_metadata(request_obj: ApprovalRequest, extra: dict[str, Any]) -> None:
    merged = dict(request_obj.metadata_json or {})
    merged.update(extra)
    request_obj.metadata_json = merged


def decide_approval_request(
    *,
    request_obj: ApprovalRequest,
    user: User,
    decision: str,
    note: str | None = None,
) -> dict[str, Any]:
    normalized_decision = (decision or "").strip().lower()
    if normalized_decision not in {"approve", "reject"}:
        raise ApprovalDecisionError("Nieprawidlowy typ decyzji.")

    expire_due_approval_requests(limit=100)

    if request_obj.status != "pending":
        raise ApprovalDecisionError("Ten wniosek nie oczekuje juz na decyzje.")
    if request_obj.expires_at is not None and request_obj.expires_at <= datetime.utcnow():
        request_obj.status = "expired"
        raise ApprovalDecisionError("Wniosek wygasl i nie moze byc juz zatwierdzony.")

    if not _role_allows_decision(request_obj, user):
        raise ApprovalDecisionError("Brak uprawnien do zatwierdzenia tego wniosku.")

    if not _allow_self_approval() and request_obj.requested_by_user_id == user.id:
        raise ApprovalDecisionError("Self-approval jest zablokowany dla tego workflow.")

    existing = ApprovalDecision.query.filter_by(approval_request_id=request_obj.id, decided_by_user_id=user.id).first()
    if existing is not None:
        raise ApprovalDecisionError("Ten operator juz podjal decyzje dla tego wniosku.")

    decision_row = ApprovalDecision(
        approval_request=request_obj,
        decided_by=user,
        decision=normalized_decision,
        note=(note or "").strip()[:255] or None,
    )
    db.session.add(decision_row)
    db.session.flush()

    now = datetime.utcnow()
    approvals = _count_approvals(request_obj)
    rejections = _count_rejections(request_obj)
    if rejections > 0:
        request_obj.status = "rejected"
        request_obj.rejected_at = now
    elif approvals >= max(1, int(request_obj.required_approvals or 1)):
        request_obj.status = "approved"
        request_obj.approved_at = now

    log_activity(
        "approvals.request_decision",
        "approval_request",
        f"Decyzja {normalized_decision} dla wniosku #{request_obj.id}",
        entity_id=request_obj.id,
        client=request_obj.client,
        actor=user,
        metadata={
            "decision": normalized_decision,
            "approvals": approvals,
            "rejections": rejections,
            "required_approvals": request_obj.required_approvals,
            "status": request_obj.status,
        },
        success=normalized_decision == "approve",
    )

    executed_payload = None
    if request_obj.status == "approved":
        executed_payload = execute_approved_request(request_obj=request_obj, actor=user)

    return {
        "status": request_obj.status,
        "approvals": approvals,
        "rejections": rejections,
        "required_approvals": request_obj.required_approvals,
        "executed_payload": executed_payload,
    }


def execute_approved_request(*, request_obj: ApprovalRequest, actor: User | None) -> dict[str, Any]:
    if request_obj.status == "executed":
        return dict((request_obj.metadata_json or {}).get("execution", {}))

    if request_obj.status != "approved":
        raise ApprovalExecutionError("Wniosek nie ma statusu approved.")

    handler = _EXECUTORS.get(request_obj.action_key)
    if handler is None:
        raise ApprovalExecutionError(f"Brak wykonawcy dla akcji {request_obj.action_key}.")

    try:
        payload = handler(request_obj, actor)
    except Exception as exc:
        request_obj.execution_error = str(exc)[:500]
        log_activity(
            "approvals.request_execute_failed",
            "approval_request",
            f"Wykonanie wniosku #{request_obj.id} nie powiodlo sie",
            entity_id=request_obj.id,
            client=request_obj.client,
            actor=actor,
            metadata={"error": request_obj.execution_error, "action_key": request_obj.action_key},
            success=False,
        )
        raise ApprovalExecutionError(str(exc)) from exc

    request_obj.status = "executed"
    request_obj.executed_at = datetime.utcnow()
    request_obj.executed_by = actor
    request_obj.execution_error = None
    _update_request_metadata(request_obj, {"execution": payload})

    log_activity(
        "approvals.request_executed",
        "approval_request",
        f"Wykonano wniosek #{request_obj.id}",
        entity_id=request_obj.id,
        client=request_obj.client,
        actor=actor,
        metadata={"action_key": request_obj.action_key, "execution": payload},
    )
    return payload


def _execute_domain_delete(request_obj: ApprovalRequest, actor: User | None) -> dict[str, Any]:
    if not str(request_obj.target_id).isdigit():
        raise ApprovalExecutionError("Nieprawidlowe ID domeny we wniosku.")

    domain_id = int(request_obj.target_id)
    domain = Domain.query.get(domain_id)
    if domain is None:
        return {"status": "skipped", "reason": "domain_missing", "domain_id": domain_id}

    client = domain.client
    domain_name = domain.name
    db.session.delete(domain)
    db.session.flush()

    apache_result: dict[str, Any] = {"status": "skipped", "enabled": False}
    try:
        apache_result = sync_client_apache_instance(client, reason="domains.delete.approved", actor=actor)
    except ClientApacheServiceError as exc:
        apache_result = {"status": "failed", "enabled": True, "message": str(exc)}

    log_activity(
        "domains.delete",
        "domain",
        f"Usunieto domene {domain_name} po akceptacji",
        entity_id=domain_id,
        client=client,
        actor=actor,
        metadata={"approval_request_id": request_obj.id, "apache_sync": apache_result},
    )
    return {
        "status": "executed",
        "domain_id": domain_id,
        "domain_name": domain_name,
        "apache_sync": apache_result,
    }


def _execute_backup_restore(request_obj: ApprovalRequest, actor: User | None) -> dict[str, Any]:
    if not str(request_obj.target_id).isdigit():
        raise ApprovalExecutionError("Nieprawidlowe ID backupu we wniosku.")

    backup_id = int(request_obj.target_id)
    backup = Backup.query.get(backup_id)
    if backup is None:
        return {"status": "skipped", "reason": "backup_missing", "backup_id": backup_id}

    requested_by = request_obj.requested_by or actor
    if requested_by is None:
        raise ApprovalExecutionError("Brak uzytkownika inicjujacego restore.")

    job = create_restore_job(backup=backup, requested_by=requested_by)
    process_restore_job(job)

    log_activity(
        "backups.restore_approved",
        "backup_restore_job",
        f"Wykonano restore backupu #{backup.id} po akceptacji",
        entity_id=job.id,
        client=backup.client,
        actor=actor,
        metadata={"approval_request_id": request_obj.id, "backup_id": backup.id, "status": job.status},
        success=job.status != "failed",
    )
    return {
        "status": "executed",
        "backup_id": backup.id,
        "job_id": job.id,
        "job_status": job.status,
        "restore_type": job.restore_type,
    }


_EXECUTORS: dict[str, _EXECUTOR] = {
    "domains.delete": _execute_domain_delete,
    "backups.restore": _execute_backup_restore,
}
