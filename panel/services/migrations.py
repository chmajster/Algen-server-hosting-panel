from __future__ import annotations

import json
from datetime import datetime

from panel.extensions import db
from panel.models import Client, MigrationJob, User


MIGRATION_STEPS: tuple[str, ...] = (
    "preflight",
    "transfer",
    "verify",
    "finalize",
)


def _sanitize(value: str | None, *, max_length: int = 255) -> str:
    return (value or "").strip()[:max_length]


def _masked_login(value: str) -> str:
    cleaned = _sanitize(value, max_length=120)
    if len(cleaned) <= 2:
        return "**"
    return f"{cleaned[0]}***{cleaned[-1]}"


def build_masked_summary(*, provider: str, hostname: str, username: str) -> str:
    return f"{provider.upper()} | {hostname} | {_masked_login(username)}"


def create_migration_job(
    *,
    client: Client,
    requested_by: User,
    source_provider: str,
    source_hostname: str,
    source_username: str,
    source_password: str | None,
    source_path: str | None,
    notes: str | None,
) -> tuple[MigrationJob | None, str | None]:
    provider = _sanitize(source_provider, max_length=64).lower()
    hostname = _sanitize(source_hostname)
    username = _sanitize(source_username, max_length=120)
    path = _sanitize(source_path)
    note_text = _sanitize(notes, max_length=1000)

    if not provider or not hostname or not username:
        return None, "Uzupelnij wymagane dane migracji."
    if " " in hostname:
        return None, "Adres serwera nie moze zawierac spacji."

    payload = {
        "source_hostname": hostname,
        "source_username": username,
        "source_path": path or None,
        "notes": note_text or None,
        "has_password": bool(_sanitize(source_password)),
    }

    job = MigrationJob(
        client=client,
        requested_by=requested_by,
        source_provider=provider,
        status="queued",
        current_step=MIGRATION_STEPS[0],
        progress_percent=0,
        payload_encrypted=json.dumps(payload, ensure_ascii=True),
        masked_summary=build_masked_summary(provider=provider, hostname=hostname, username=username),
        metadata_json={"events": [{"state": "queued", "at": datetime.utcnow().isoformat()}]},
    )
    db.session.add(job)
    return job, None


def _append_event(job: MigrationJob, state: str, message: str) -> None:
    metadata = dict(job.metadata_json or {})
    events = list(metadata.get("events") or [])
    events.append({"state": state, "message": message, "at": datetime.utcnow().isoformat()})
    metadata["events"] = events
    job.metadata_json = metadata


def advance_migration_job(job: MigrationJob) -> bool:
    if job.status in {"completed", "failed", "cancelled"}:
        return False

    if job.status == "queued":
        job.status = "running"
        if job.started_at is None:
            job.started_at = datetime.utcnow()
        _append_event(job, "running", "Rozpoczeto przetwarzanie migracji.")

    try:
        current_index = MIGRATION_STEPS.index(job.current_step)
    except ValueError:
        current_index = 0
        job.current_step = MIGRATION_STEPS[0]

    if current_index >= len(MIGRATION_STEPS) - 1:
        job.current_step = "done"
        job.progress_percent = 100
        job.status = "completed"
        job.finished_at = datetime.utcnow()
        job.last_error = None
        _append_event(job, "completed", "Migracja zakonczona pomyslnie.")
        return True

    next_index = current_index + 1
    job.current_step = MIGRATION_STEPS[next_index]
    job.progress_percent = int((next_index / len(MIGRATION_STEPS)) * 100)
    _append_event(job, "step", f"Przejscie do kroku {job.current_step}.")
    return True


def cancel_migration_job(job: MigrationJob, *, reason: str) -> bool:
    if job.status in {"completed", "failed", "cancelled"}:
        return False
    job.status = "cancelled"
    job.finished_at = datetime.utcnow()
    job.last_error = reason[:500]
    _append_event(job, "cancelled", reason)
    return True


def run_due_migration_jobs(*, limit: int = 20) -> int:
    jobs = (
        MigrationJob.query.filter(MigrationJob.status.in_(["queued", "running"]))
        .order_by(MigrationJob.created_at.asc())
        .limit(max(1, limit))
        .all()
    )
    processed = 0
    for job in jobs:
        if advance_migration_job(job):
            processed += 1
    return processed
