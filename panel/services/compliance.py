from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, or_

from panel.extensions import db
from panel.models import (
    ActivityLog,
    ApprovalRequest,
    Backup,
    Client,
    ComplianceChecklistItem,
    ComplianceEvidenceLink,
    ComplianceResult,
    ComplianceRun,
    DisasterRecoveryProfile,
    RetentionCleanupRun,
    StatusEvent,
    User,
    VaultSecret,
    WebhookDelivery,
    WebhookEndpoint,
)
from panel.services.audit import verify_activity_chain
from panel.services.secrets_vault import due_rotation_secrets


COMPLIANCE_CONTROL_STATES = {
    "not_started",
    "in_progress",
    "compliant",
    "partial",
    "non_compliant",
}
COMPLIANCE_EVIDENCE_TYPES = {"audit_log", "backup", "incident"}


def normalize_control_status(value: str | None) -> str:
    candidate = (value or "").strip().lower()
    if candidate in COMPLIANCE_CONTROL_STATES:
        return candidate
    return "not_started"


def list_checklist_items(*, client_id: int | None = None, limit: int = 300) -> list[ComplianceChecklistItem]:
    query = ComplianceChecklistItem.query.order_by(ComplianceChecklistItem.due_date.asc(), ComplianceChecklistItem.id.asc())
    if client_id is None:
        query = query.filter(ComplianceChecklistItem.client_id.is_(None))
    else:
        query = query.filter(ComplianceChecklistItem.client_id == client_id)
    return query.limit(max(1, int(limit))).all()


def upsert_checklist_item(
    *,
    client_id: int | None,
    control_code: str,
    title: str,
    description: str | None,
    status: str,
    owner: User | None,
    due_date: date | None,
    actor: User | None,
) -> ComplianceChecklistItem:
    normalized_code = (control_code or "").strip().lower()[:64]
    if not normalized_code:
        raise ValueError("Kod kontrolki compliance jest wymagany.")

    normalized_title = (title or "").strip()[:160]
    if not normalized_title:
        raise ValueError("Tytul kontrolki compliance jest wymagany.")

    row = ComplianceChecklistItem.query.filter_by(client_id=client_id, control_code=normalized_code).first()
    if row is None:
        row = ComplianceChecklistItem(
            client_id=client_id,
            control_code=normalized_code,
            created_by=actor,
        )
        db.session.add(row)

    row.title = normalized_title
    row.description = (description or "").strip()[:255] or None
    row.status = normalize_control_status(status)
    row.owner = owner
    row.due_date = due_date
    row.updated_by = actor
    return row


def _resolve_evidence_reference(*, evidence_type: str, reference_id: str, client_id: int | None) -> tuple[bool, str]:
    normalized_type = (evidence_type or "").strip().lower()
    if normalized_type not in COMPLIANCE_EVIDENCE_TYPES:
        return False, "Nieobslugiwany typ evidence."

    raw_ref = (reference_id or "").strip()
    if not raw_ref.isdigit():
        return False, "reference_id musi byc liczba calkowita."

    ref_id = int(raw_ref)
    if normalized_type == "audit_log":
        row = ActivityLog.query.get(ref_id)
        if row is None:
            return False, "Audit log evidence nie istnieje."
        if client_id is None and row.client_id is not None:
            return False, "Evidence audit log nalezy do tenanta i nie pasuje do scope global."
        if client_id is not None and row.client_id != client_id:
            return False, "Evidence audit log nie nalezy do wybranego tenanta."
        return True, f"activity:{row.id}:{row.action}"

    if normalized_type == "backup":
        row = Backup.query.get(ref_id)
        if row is None:
            return False, "Backup evidence nie istnieje."
        if client_id is None and row.client_id is not None:
            return False, "Evidence backup nalezy do tenanta i nie pasuje do scope global."
        if client_id is not None and row.client_id != client_id:
            return False, "Evidence backup nie nalezy do wybranego tenanta."
        return True, f"backup:{row.id}:{row.status}"

    row = StatusEvent.query.get(ref_id)
    if row is None:
        return False, "Incident evidence nie istnieje."
    if client_id is not None and not bool(row.is_public):
        return False, "Niepubliczny incident nie moze byc linked do tenant checklist."
    return True, f"incident:{row.id}:{row.state}"


def link_checklist_evidence(
    *,
    checklist_item: ComplianceChecklistItem,
    evidence_type: str,
    reference_id: str,
    actor: User | None,
) -> ComplianceEvidenceLink:
    ok, label_or_error = _resolve_evidence_reference(
        evidence_type=evidence_type,
        reference_id=reference_id,
        client_id=checklist_item.client_id,
    )
    if not ok:
        raise ValueError(label_or_error)

    normalized_type = (evidence_type or "").strip().lower()
    normalized_ref = (reference_id or "").strip()

    existing = ComplianceEvidenceLink.query.filter_by(
        checklist_item_id=checklist_item.id,
        evidence_type=normalized_type,
        reference_id=normalized_ref,
    ).first()
    if existing is not None:
        return existing

    row = ComplianceEvidenceLink(
        checklist_item=checklist_item,
        client_id=checklist_item.client_id,
        evidence_type=normalized_type,
        reference_id=normalized_ref,
        reference_label=label_or_error,
        linked_by=actor,
    )
    db.session.add(row)
    return row


def _result(
    *,
    check_code: str,
    status: str,
    severity: str,
    score: int,
    message: str,
    details: dict | None = None,
    evidence_ref: str | None = None,
) -> dict:
    return {
        "check_code": check_code,
        "status": status,
        "severity": severity,
        "score": max(0, min(100, int(score))),
        "message": (message or "")[:255],
        "details": details or {},
        "evidence_ref": (evidence_ref or "")[:255] or None,
    }


def _check_audit_chain() -> dict:
    outcome = verify_activity_chain(max_errors=100)
    if outcome.get("valid"):
        return _result(
            check_code="audit_chain_integrity",
            status="pass",
            severity="high",
            score=100,
            message="Audit chain integrity OK",
            details={"checked": outcome.get("checked", 0), "legacy_rows": outcome.get("legacy_rows", 0)},
            evidence_ref=f"latest_sequence:{outcome.get('latest_sequence', 0)}",
        )
    return _result(
        check_code="audit_chain_integrity",
        status="fail",
        severity="critical",
        score=20,
        message="Audit chain integrity violations detected",
        details={"errors": outcome.get("errors", [])[:20], "checked": outcome.get("checked", 0)},
        evidence_ref="admin.audit_integrity",
    )


def _check_expired_pending_approvals() -> dict:
    now = datetime.utcnow()
    count = (
        ApprovalRequest.query.filter(ApprovalRequest.status == "pending")
        .filter(ApprovalRequest.expires_at.is_not(None))
        .filter(ApprovalRequest.expires_at < now)
        .count()
    )
    if count == 0:
        return _result(
            check_code="approval_queue_freshness",
            status="pass",
            severity="medium",
            score=100,
            message="No expired pending approvals",
            details={"expired_pending": 0},
        )
    return _result(
        check_code="approval_queue_freshness",
        status="warn",
        severity="medium",
        score=65,
        message=f"Expired pending approvals: {count}",
        details={"expired_pending": count},
        evidence_ref="admin.approvals",
    )


def _check_dead_letter_webhooks(*, client_id: int | None) -> dict:
    query = WebhookDelivery.query.filter(WebhookDelivery.dead_lettered.is_(True))
    if client_id is not None:
        query = query.join(WebhookEndpoint, WebhookDelivery.endpoint_id == WebhookEndpoint.id).filter(
            WebhookEndpoint.client_id == client_id
        )
    count = query.count()
    if count == 0:
        return _result(
            check_code="webhooks_dead_letter",
            status="pass",
            severity="medium",
            score=100,
            message="No dead-letter webhooks",
            details={"dead_lettered": 0},
        )
    return _result(
        check_code="webhooks_dead_letter",
        status="warn",
        severity="medium",
        score=70,
        message=f"Dead-letter webhooks pending replay: {count}",
        details={"dead_lettered": count},
        evidence_ref="admin.webhooks",
    )


def _check_due_secret_rotation(*, client_id: int | None) -> dict:
    query = VaultSecret.query.filter(VaultSecret.status == "active")
    if client_id is not None:
        query = query.filter(VaultSecret.client_id == client_id)
    due_count = (
        query.filter(VaultSecret.next_rotation_due_at.is_not(None))
        .filter(VaultSecret.next_rotation_due_at <= datetime.utcnow())
        .count()
    )
    if due_count == 0:
        return _result(
            check_code="secrets_rotation_due",
            status="pass",
            severity="high",
            score=100,
            message="No secrets are overdue for rotation",
            details={"due": 0},
        )
    return _result(
        check_code="secrets_rotation_due",
        status="warn",
        severity="high",
        score=60,
        message=f"Secrets due for rotation: {due_count}",
        details={"due": due_count},
        evidence_ref="admin.governance_secrets",
    )


def _check_retention_cleanup_freshness() -> dict:
    latest = (
        RetentionCleanupRun.query.filter_by(status="completed")
        .order_by(RetentionCleanupRun.finished_at.desc(), RetentionCleanupRun.id.desc())
        .first()
    )
    if latest is None or latest.finished_at is None:
        return _result(
            check_code="retention_cleanup_freshness",
            status="fail",
            severity="high",
            score=30,
            message="Retention cleanup has never completed",
            details={},
            evidence_ref="admin.governance_retention",
        )
    age = datetime.utcnow() - latest.finished_at
    if age <= timedelta(hours=24):
        return _result(
            check_code="retention_cleanup_freshness",
            status="pass",
            severity="high",
            score=100,
            message="Retention cleanup executed within last 24h",
            details={"hours_since_last_run": round(age.total_seconds() / 3600, 2), "run_id": latest.id},
            evidence_ref=f"retention_run:{latest.id}",
        )
    return _result(
        check_code="retention_cleanup_freshness",
        status="warn",
        severity="high",
        score=55,
        message="Retention cleanup is stale (>24h)",
        details={"hours_since_last_run": round(age.total_seconds() / 3600, 2), "run_id": latest.id},
        evidence_ref=f"retention_run:{latest.id}",
    )


def _check_backup_verification(*, client_id: int | None) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=7)
    query = Backup.query.filter(Backup.status == "completed")
    if client_id is not None:
        query = query.filter(Backup.client_id == client_id)
    stale_count = (
        query.filter(
            or_(
                Backup.last_verified_at.is_(None),
                and_(Backup.last_verified_at.is_not(None), Backup.last_verified_at < cutoff),
            )
        )
        .count()
    )
    if stale_count == 0:
        return _result(
            check_code="backup_verification_recent",
            status="pass",
            severity="high",
            score=100,
            message="All completed backups verified recently",
            details={"stale_or_missing_verification": 0},
        )
    return _result(
        check_code="backup_verification_recent",
        status="warn",
        severity="high",
        score=65,
        message=f"Backups without recent verification: {stale_count}",
        details={"stale_or_missing_verification": stale_count},
        evidence_ref="admin_backups",
    )


def _check_dr_profile_coverage(*, client_id: int | None) -> dict:
    if client_id is not None:
        expected = 1
        covered = DisasterRecoveryProfile.query.filter_by(client_id=client_id).count()
    else:
        expected = Client.query.count()
        covered = DisasterRecoveryProfile.query.count()

    if expected <= 0:
        return _result(
            check_code="dr_profile_coverage",
            status="pass",
            severity="medium",
            score=100,
            message="No tenants to evaluate for DR profile coverage",
            details={"covered": covered, "expected": expected},
        )

    ratio = covered / expected
    if ratio >= 1.0:
        return _result(
            check_code="dr_profile_coverage",
            status="pass",
            severity="medium",
            score=100,
            message="DR profiles configured for all tenants",
            details={"covered": covered, "expected": expected},
        )
    if ratio >= 0.6:
        return _result(
            check_code="dr_profile_coverage",
            status="warn",
            severity="medium",
            score=70,
            message="DR profile coverage is partial",
            details={"covered": covered, "expected": expected},
            evidence_ref="admin.governance_dr",
        )
    return _result(
        check_code="dr_profile_coverage",
        status="fail",
        severity="medium",
        score=40,
        message="DR profile coverage is low",
        details={"covered": covered, "expected": expected},
        evidence_ref="admin.governance_dr",
    )


def run_compliance_checks(*, actor: User | None = None, client_id: int | None = None) -> ComplianceRun:
    run = ComplianceRun(
        client_id=client_id,
        status="running",
        started_at=datetime.utcnow(),
        triggered_by=actor,
        summary_json={},
    )
    db.session.add(run)
    db.session.flush()

    checks = [
        _check_audit_chain(),
        _check_expired_pending_approvals(),
        _check_dead_letter_webhooks(client_id=client_id),
        _check_due_secret_rotation(client_id=client_id),
        _check_retention_cleanup_freshness(),
        _check_backup_verification(client_id=client_id),
        _check_dr_profile_coverage(client_id=client_id),
    ]

    statuses = {item["status"] for item in checks}
    if "fail" in statuses:
        final_status = "failed"
    elif "warn" in statuses:
        final_status = "warning"
    else:
        final_status = "passed"

    score = int(sum(item["score"] for item in checks) / max(1, len(checks)))

    for item in checks:
        db.session.add(
            ComplianceResult(
                run=run,
                client_id=client_id,
                check_code=item["check_code"],
                status=item["status"],
                severity=item["severity"],
                score=item["score"],
                message=item["message"],
                details_json=item["details"],
                evidence_ref=item["evidence_ref"],
            )
        )

    run.status = final_status
    run.score = score
    run.finished_at = datetime.utcnow()
    run.summary_json = {
        "checks": checks,
        "final_status": final_status,
        "score": score,
        "client_id": client_id,
    }
    return run
