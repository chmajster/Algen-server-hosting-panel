from __future__ import annotations

import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from flask import current_app

from panel.extensions import db
from panel.models import Backup, BackupVerificationRun
from panel.services.audit import log_activity
from panel.services.backup_storage import _target_client
from panel.services.mailer import send_plain_email
from panel.services.webhooks import dispatch_webhook_event


def _local_backup_path(backup: Backup) -> Path:
    source = Path(backup.storage_path)
    if source.is_absolute():
        return source
    return Path(current_app.config.get("BACKUP_ROOT", "storage/backups")) / source


def _verify_local_backup(backup: Backup) -> tuple[bool, str]:
    source = _local_backup_path(backup)
    if not source.exists():
        return False, "Brak lokalnego artefaktu backupu."

    if source.is_dir():
        has_data = any(source.rglob("*"))
        if not has_data:
            return False, "Backup katalogowy jest pusty."
        return True, "Backup katalogowy jest poprawny."

    if source.stat().st_size <= 0:
        return False, "Backup plikowy ma rozmiar 0 B."

    suffix = source.suffix.lower()
    archive_like = suffix in {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz"} or source.name.endswith(".tar.gz")
    if not archive_like:
        return True, "Backup plikowy jest dostepny."

    temp_dir = Path(tempfile.mkdtemp(prefix="backup-verify-"))
    try:
        shutil.unpack_archive(str(source), str(temp_dir))
        has_data = any(temp_dir.rglob("*"))
        if not has_data:
            return False, "Archiwum backupu jest puste po test restore."
        return True, "Archiwum backupu poprawnie rozpakowane."
    except Exception as exc:
        return False, f"Nie mozna zweryfikowac archiwum backupu: {exc}"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _verify_external_backup(backup: Backup) -> tuple[bool, str]:
    target = backup.storage_target
    if target is None:
        return False, "Brak skonfigurowanego targetu storage."
    if not backup.external_location:
        return False, "Brak zewnetrznej lokalizacji backupu."

    external = backup.external_location
    if not external.startswith("s3://"):
        return False, "Nieobslugiwany format lokalizacji zewnetrznej backupu."

    _, _, path_part = external.partition("s3://")
    bucket, _, key = path_part.partition("/")
    if not bucket or not key:
        return False, "Nieprawidlowy format lokalizacji zewnetrznej backupu."

    try:
        client = _target_client(target)
        client.head_object(Bucket=bucket, Key=key)
        return True, "Artefakt backupu istnieje w storage zewnetrznym."
    except Exception as exc:
        return False, f"Nie mozna zweryfikowac backupu w storage: {exc}"


def _verification_failure_streak(backup: Backup) -> int:
    rows = (
        BackupVerificationRun.query.filter_by(backup_id=backup.id)
        .order_by(BackupVerificationRun.created_at.desc(), BackupVerificationRun.id.desc())
        .limit(20)
        .all()
    )
    streak = 0
    for row in rows:
        if row.status == "failed":
            streak += 1
        else:
            break
    return streak


def _notify_repeated_verification_failures(backup: Backup, streak: int, message: str) -> None:
    threshold = max(2, int(current_app.config.get("BACKUP_VERIFICATION_FAILURE_ALERT_THRESHOLD", 3)))
    if streak < threshold:
        return

    payload = {
        "backup_id": backup.id,
        "client_id": backup.client_id,
        "streak": streak,
        "message": message,
        "external_location": backup.external_location,
    }
    dispatch_webhook_event("backup.verification_failed", payload, client=backup.client, auto_commit=False)

    admin_email = str(current_app.config.get("BACKUP_ALERT_ADMIN_EMAIL", "") or "").strip()
    if admin_email:
        send_plain_email(
            to_email=admin_email,
            subject=f"[Hosting Panel] Powtarzajaca sie awaria weryfikacji backupu #{backup.id}",
            body=(
                f"Backup #{backup.id} klienta {backup.client_id} ma ciag niepowodzen: {streak}.\n"
                f"Szczegoly: {message}\n"
            ),
        )

    log_activity(
        "backups.verification_repeated_failure",
        "backup",
        f"Powtarzajaca sie awaria weryfikacji backupu #{backup.id}",
        entity_id=backup.id,
        client=backup.client,
        metadata={"streak": streak, "message": message},
        success=False,
    )


def verify_backup(backup: Backup, *, schedule_type: str = "daily") -> BackupVerificationRun:
    run = BackupVerificationRun(
        backup=backup,
        status="running",
        schedule_type=schedule_type,
    )
    db.session.add(run)
    db.session.flush()

    started = time.perf_counter()
    if backup.external_location:
        ok, message = _verify_external_backup(backup)
    else:
        ok, message = _verify_local_backup(backup)
    duration_ms = int((time.perf_counter() - started) * 1000)

    run.status = "success" if ok else "failed"
    run.verified_at = datetime.utcnow()
    run.restore_duration_ms = duration_ms
    run.validation_message = message[:500]

    backup.last_verified_at = run.verified_at
    backup.last_verification_status = run.status
    backup.last_verification_message = run.validation_message

    log_activity(
        "backups.verify",
        "backup_verification",
        f"Weryfikacja backupu #{backup.id}: {run.status}",
        entity_id=run.id,
        client=backup.client,
        metadata={
            "schedule_type": schedule_type,
            "duration_ms": duration_ms,
            "message": run.validation_message,
        },
        success=ok,
    )

    if not ok:
        streak = _verification_failure_streak(backup)
        _notify_repeated_verification_failures(backup, streak, run.validation_message or "")

    return run


def _should_verify_backup(backup: Backup, schedule_type: str) -> bool:
    if backup.status in {"deleted", "failed"}:
        return False
    if schedule_type == "weekly":
        interval = timedelta(days=7)
    else:
        interval = timedelta(days=1)
    if backup.last_verified_at is None:
        return True
    return backup.last_verified_at <= datetime.utcnow() - interval


def run_verification_schedule(*, schedule_type: str = "daily", limit: int = 50) -> dict:
    backups = (
        Backup.query.filter(Backup.status.in_(["completed", "scheduled", "queued"]))
        .order_by(Backup.created_at.desc(), Backup.id.desc())
        .limit(max(1, int(limit)))
        .all()
    )

    processed = 0
    success = 0
    failed = 0
    for backup in backups:
        if not _should_verify_backup(backup, schedule_type):
            continue
        run = verify_backup(backup, schedule_type=schedule_type)
        processed += 1
        if run.status == "success":
            success += 1
        else:
            failed += 1

    return {
        "processed": processed,
        "success": success,
        "failed": failed,
        "schedule_type": schedule_type,
    }
