from __future__ import annotations

from datetime import datetime, timedelta

from panel.extensions import db
from panel.models import Backup, Client, DisasterRecoveryCheckRun, DisasterRecoveryProfile, User


def get_or_create_dr_profile(client: Client) -> DisasterRecoveryProfile:
    profile = DisasterRecoveryProfile.query.filter_by(client_id=client.id).first()
    if profile is None:
        profile = DisasterRecoveryProfile(
            client=client,
            rpo_target_minutes=1440,
            rto_target_minutes=240,
        )
        db.session.add(profile)
        db.session.flush()
    return profile


def update_dr_profile(
    *,
    client: Client,
    primary_region: str | None,
    secondary_region: str | None,
    rpo_target_minutes: int,
    rto_target_minutes: int,
    notes: str | None,
) -> DisasterRecoveryProfile:
    profile = get_or_create_dr_profile(client)
    profile.primary_region = (primary_region or "").strip()[:64] or None
    profile.secondary_region = (secondary_region or "").strip()[:64] or None
    profile.rpo_target_minutes = max(1, int(rpo_target_minutes or 1440))
    profile.rto_target_minutes = max(1, int(rto_target_minutes or 240))
    profile.notes = (notes or "").strip()[:255] or None
    return profile


def _latest_completed_backup(client: Client) -> Backup | None:
    return (
        Backup.query.filter_by(client_id=client.id)
        .filter(Backup.status == "completed")
        .order_by(Backup.completed_at.desc(), Backup.created_at.desc(), Backup.id.desc())
        .first()
    )


def evaluate_dr_readiness(client: Client) -> dict:
    profile = DisasterRecoveryProfile.query.filter_by(client_id=client.id).first()
    rpo_target = int(profile.rpo_target_minutes) if profile is not None and profile.rpo_target_minutes else 1440
    rto_target = int(profile.rto_target_minutes) if profile is not None and profile.rto_target_minutes else 240
    latest_backup = _latest_completed_backup(client)
    now = datetime.utcnow()
    profile_meta = dict(profile.metadata_json or {}) if profile is not None and isinstance(profile.metadata_json, dict) else {}

    if latest_backup is not None:
        reference_time = latest_backup.completed_at or latest_backup.created_at
        age_minutes = int(max(0, (now - reference_time).total_seconds() / 60)) if reference_time else None
    else:
        age_minutes = None

    has_recent_verification = bool(
        latest_backup
        and latest_backup.last_verified_at is not None
        and latest_backup.last_verified_at >= now - timedelta(days=7)
    )

    regions: set[str] = set()
    if latest_backup is not None and latest_backup.storage_target is not None and latest_backup.storage_target.region:
        regions.add(latest_backup.storage_target.region)
    if profile is not None and profile.primary_region:
        regions.add(profile.primary_region)
    if profile is not None and profile.secondary_region:
        regions.add(profile.secondary_region)

    multi_region = len(regions) >= 2

    estimated_rpo = age_minutes if age_minutes is not None else 10**9
    if has_recent_verification and estimated_rpo < 10**9:
        estimated_rto = 120
    elif latest_backup is not None:
        estimated_rto = 240
    else:
        estimated_rto = 10**9

    meets_rpo = estimated_rpo <= rpo_target
    meets_rto = estimated_rto <= rto_target

    score = 0
    if latest_backup is not None:
        score += 30
    if has_recent_verification:
        score += 20
    if multi_region:
        score += 20
    if meets_rpo:
        score += 15
    if meets_rto:
        score += 15

    if latest_backup is None and profile is None:
        status = "unknown"
    elif score >= 80:
        status = "healthy"
    elif score >= 55:
        status = "degraded"
    else:
        status = "critical"

    if latest_backup is None:
        replication_status = "unknown"
    elif multi_region and meets_rpo:
        replication_status = "healthy"
    elif multi_region or meets_rpo:
        replication_status = "degraded"
    else:
        replication_status = "critical"

    last_failover_test_result = str(profile_meta.get("last_failover_test_result") or "unknown")

    return {
        "client_id": client.id,
        "client_username": client.user.username if client.user else str(client.id),
        "status": status,
        "score": score,
        "replication_status": replication_status,
        "replication_source": profile.primary_region if profile is not None else None,
        "replication_target": profile.secondary_region if profile is not None else None,
        "last_sync_at": (latest_backup.completed_at or latest_backup.created_at).isoformat() if latest_backup else None,
        "latest_backup_id": latest_backup.id if latest_backup else None,
        "latest_backup_at": (latest_backup.completed_at or latest_backup.created_at).isoformat() if latest_backup else None,
        "latest_backup_age_minutes": age_minutes,
        "has_recent_verification": has_recent_verification,
        "regions": sorted(regions),
        "multi_region": multi_region,
        "rpo_target_minutes": rpo_target,
        "rto_target_minutes": rto_target,
        "rpo_minutes": int(estimated_rpo if estimated_rpo < 10**9 else -1),
        "rto_minutes": int(estimated_rto if estimated_rto < 10**9 else -1),
        "meets_rpo": meets_rpo,
        "meets_rto": meets_rto,
        "failover_readiness_status": status,
        "last_failover_test_result": last_failover_test_result,
        "last_failover_test_at": profile.failover_last_tested_at.isoformat()
        if profile is not None and profile.failover_last_tested_at
        else None,
    }


def run_dr_readiness_checks(*, actor: User | None = None, client_id: int | None = None, limit: int = 200) -> dict:
    query = Client.query.order_by(Client.id.asc())
    if client_id is not None:
        query = query.filter(Client.id == client_id)

    clients = query.limit(max(1, int(limit))).all()
    snapshots: list[dict] = []

    for client in clients:
        snapshot = evaluate_dr_readiness(client)

        previous = (
            DisasterRecoveryCheckRun.query.filter_by(client_id=client.id)
            .order_by(DisasterRecoveryCheckRun.checked_at.desc(), DisasterRecoveryCheckRun.id.desc())
            .first()
        )
        previous_status = previous.status if previous is not None else None
        snapshot["status_changed"] = previous_status is not None and previous_status != snapshot["status"]
        snapshot["previous_status"] = previous_status

        snapshots.append(snapshot)
        db.session.add(
            DisasterRecoveryCheckRun(
                client=client,
                status=snapshot["status"],
                score=snapshot["score"],
                rpo_minutes=snapshot["rpo_minutes"] if snapshot["rpo_minutes"] >= 0 else None,
                rto_minutes=snapshot["rto_minutes"] if snapshot["rto_minutes"] >= 0 else None,
                message=(
                    f"DR readiness {snapshot['status']} ({snapshot['score']})"
                    + (f" [changed from {previous_status}]" if snapshot["status_changed"] else "")
                ),
                details_json=snapshot,
                checked_at=datetime.utcnow(),
                checked_by=actor,
            )
        )

    overall_score = int(sum(item["score"] for item in snapshots) / max(1, len(snapshots))) if snapshots else 0

    return {
        "clients": len(snapshots),
        "overall_score": overall_score,
        "snapshots": snapshots,
    }


def run_failover_simulation(*, client: Client, actor: User | None = None, safe_mode: bool = True) -> dict:
    profile = get_or_create_dr_profile(client)
    snapshot = evaluate_dr_readiness(client)
    now = datetime.utcnow()

    passed = snapshot["status"] in {"healthy", "degraded"} and bool(snapshot.get("multi_region"))
    result = "passed" if passed else "failed"

    profile.failover_last_tested_at = now
    metadata_json = dict(profile.metadata_json or {})
    metadata_json["last_failover_test_result"] = result
    metadata_json["last_failover_test_at"] = now.isoformat()
    metadata_json["last_failover_safe_mode"] = bool(safe_mode)
    metadata_json["last_failover_snapshot"] = snapshot
    profile.metadata_json = metadata_json

    run = DisasterRecoveryCheckRun(
        client=client,
        status="healthy" if passed else "critical",
        score=snapshot.get("score"),
        rpo_minutes=snapshot.get("rpo_minutes") if snapshot.get("rpo_minutes", -1) >= 0 else None,
        rto_minutes=snapshot.get("rto_minutes") if snapshot.get("rto_minutes", -1) >= 0 else None,
        message=f"Failover simulation {result} ({'safe' if safe_mode else 'full'})",
        details_json={
            "run_type": "failover_simulation",
            "safe_mode": bool(safe_mode),
            "result": result,
            "snapshot": snapshot,
        },
        checked_at=now,
        checked_by=actor,
    )
    db.session.add(run)
    db.session.flush()

    return {
        "run_id": run.id,
        "result": result,
        "safe_mode": bool(safe_mode),
        "snapshot": snapshot,
    }
