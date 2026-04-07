from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from flask import current_app

from panel.extensions import db
from panel.models import Backup, BackupRestoreJob, User


def _client_restore_root(client_username: str) -> Path:
    base = Path(current_app.config.get("CLIENT_HOME_ROOT", "storage/clients")) / client_username / "restores"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _backup_source(path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = Path(current_app.config.get("BACKUP_ROOT", "storage/backups")) / path
    return path


def create_restore_job(*, backup: Backup, requested_by: User) -> BackupRestoreJob:
    restore_type = "database" if backup.backup_type == "database" else "files"
    job = BackupRestoreJob(
        client=backup.client,
        backup=backup,
        requested_by=requested_by,
        status="queued",
        restore_type=restore_type,
        metadata_json={"backup_type": backup.backup_type},
    )
    db.session.add(job)
    db.session.flush()
    return job


def process_restore_job(job: BackupRestoreJob) -> BackupRestoreJob:
    job.started_at = datetime.utcnow()
    source = _backup_source(job.backup.storage_path)
    if not source.exists():
        job.status = "failed"
        job.message = "Nie znaleziono pliku backupu."
        job.finished_at = datetime.utcnow()
        return job

    if job.restore_type == "database":
        # Database restore is intentionally queued for admin-controlled execution.
        job.status = "queued"
        job.message = "Przywracanie baz danych wymaga wykonania przez administratora."
        return job

    username = job.client.user.username if job.client and job.client.user else f"client-{job.client_id}"
    target_root = _client_restore_root(username)
    target_dir = target_root / f"restore_{job.id}_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
    target_dir.mkdir(parents=True, exist_ok=True)
    job.target_path = str(target_dir)

    try:
        if source.is_dir():
            destination = target_dir / source.name
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.unpack_archive(str(source), str(target_dir))
        job.status = "completed"
        job.message = "Backup zostal przywrocony do katalogu restore klienta."
    except Exception as exc:
        job.status = "failed"
        job.message = f"Blad przywracania backupu: {exc}"
    finally:
        job.finished_at = datetime.utcnow()
    return job
