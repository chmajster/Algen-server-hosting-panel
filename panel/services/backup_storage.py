from __future__ import annotations

import os
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

from flask import current_app

from panel.models import Backup, Client, ClientService, ExternalBackupTarget


def _load_boto3():
    try:
        import boto3  # type: ignore

        return boto3
    except Exception:
        return None


def _local_backup_path(backup: Backup) -> Path:
    path = Path(backup.storage_path)
    if path.is_absolute():
        return path
    return Path(current_app.config.get("BACKUP_ROOT", "storage/backups")) / path


def _temporary_archive_from_directory(source_dir: Path) -> Path:
    temp_fd, temp_name = tempfile.mkstemp(prefix="backup-upload-", suffix=".tar.gz")
    os.close(temp_fd)
    archive_path = Path(temp_name)
    with tarfile.open(archive_path, mode="w:gz") as archive:
        archive.add(source_dir, arcname=source_dir.name)
    return archive_path


def _target_client(target: ExternalBackupTarget):
    boto3 = _load_boto3()
    if boto3 is None:
        raise RuntimeError("Brak zaleznosci boto3 do obslugi storage S3/B2.")

    access_key = os.getenv((target.access_key_env or "").strip())
    secret_key = os.getenv((target.secret_key_env or "").strip())
    if not access_key or not secret_key:
        raise RuntimeError("Brak danych dostepowych storage w zmiennych srodowiskowych.")

    client_kwargs = {
        "service_name": "s3",
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
    }
    if target.endpoint_url:
        client_kwargs["endpoint_url"] = target.endpoint_url
    if target.region:
        client_kwargs["region_name"] = target.region

    return boto3.client(**client_kwargs)


def validate_backup_target_connectivity(target: ExternalBackupTarget) -> tuple[bool, str]:
    try:
        client = _target_client(target)
        client.list_objects_v2(Bucket=target.bucket_name, MaxKeys=1)
        target.last_checked_at = datetime.utcnow()
        target.last_check_status = "ok"
        target.last_check_message = "Polaczenie z storage poprawne."
        return True, "Polaczenie z storage poprawne."
    except Exception as exc:
        target.last_checked_at = datetime.utcnow()
        target.last_check_status = "error"
        target.last_check_message = str(exc)[:500]
        return False, f"Blad polaczenia z storage: {exc}"


def upload_backup_to_target(backup: Backup) -> Backup:
    target = backup.storage_target
    if target is None:
        return backup

    source = _local_backup_path(backup)
    if not source.exists():
        backup.status = "failed"
        raise FileNotFoundError("Nie znaleziono lokalnego artefaktu backupu do uploadu.")

    upload_path = source
    cleanup_archive = None
    if source.is_dir():
        upload_path = _temporary_archive_from_directory(source)
        cleanup_archive = upload_path

    client = _target_client(target)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    key = f"client-{backup.client_id}/backup-{backup.id}-{timestamp}-{upload_path.name}"

    try:
        client.upload_file(str(upload_path), target.bucket_name, key)
        backup.external_location = f"s3://{target.bucket_name}/{key}"
        backup.status = "completed"
        backup.completed_at = datetime.utcnow()
    finally:
        if cleanup_archive is not None and cleanup_archive.exists():
            cleanup_archive.unlink(missing_ok=True)

    return backup


def resolve_client_backup_policy(client: Client) -> dict:
    service = (
        ClientService.query.filter_by(client_id=client.id, service_type="hosting")
        .filter(ClientService.status != "deleted")
        .order_by(ClientService.created_at.desc())
        .first()
    )
    if service is None or service.plan is None:
        return {
            "frequency": "daily",
            "restore_points": 7,
            "retention_days": 30,
            "storage_target_id": None,
        }

    plan = service.plan
    return {
        "frequency": plan.backup_frequency or "daily",
        "restore_points": int(plan.backup_restore_points or 7),
        "retention_days": int(plan.backup_retention_days or 30),
        "storage_target_id": plan.backup_storage_target_id,
    }


def apply_restore_points_retention(client: Client) -> int:
    policy = resolve_client_backup_policy(client)
    keep = max(1, int(policy.get("restore_points", 7)))
    backups = (
        Backup.query.filter_by(client_id=client.id)
        .order_by(Backup.created_at.desc(), Backup.id.desc())
        .all()
    )
    removed = 0
    for backup in backups[keep:]:
        if backup.status not in {"completed", "failed", "deleted"}:
            continue
        backup.status = "deleted"
        removed += 1
    return removed
