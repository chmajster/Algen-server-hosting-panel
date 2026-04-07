from __future__ import annotations

import os
import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

from flask import current_app

from panel.extensions import db
from panel.models import Client, ClientResourceSample, HostingDatabase, Mailbox
from panel.services.client_apache import client_apache_container_name


SIZE_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGTP]?i?B)\s*$", re.IGNORECASE)


def _size_to_mb(raw: str) -> float | None:
    if not raw:
        return None
    match = SIZE_RE.match(raw.strip())
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2).lower()
    scale = {
        "b": 1 / (1024 * 1024),
        "kib": 1 / 1024,
        "kb": 1 / 1024,
        "mib": 1,
        "mb": 1,
        "gib": 1024,
        "gb": 1024,
        "tib": 1024 * 1024,
        "tb": 1024 * 1024,
        "pib": 1024 * 1024 * 1024,
        "pb": 1024 * 1024 * 1024,
    }.get(unit)
    if scale is None:
        return None
    return value * scale


def _docker_stats() -> dict[str, dict[str, float | None]]:
    if not current_app.config.get("CLIENT_APACHE_ENABLED", False):
        return {}
    if shutil.which("docker") is None:
        return {}

    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=8,
    )
    if result.returncode != 0:
        return {}

    parsed: dict[str, dict[str, float | None]] = {}
    for line in result.stdout.splitlines():
        name, _, cpu_raw, mem_raw = (line + "||").split("|", 3)
        name = name.strip()
        if not name:
            continue
        cpu_value = None
        cpu_raw = cpu_raw.replace("%", "").strip()
        try:
            if cpu_raw:
                cpu_value = float(cpu_raw)
        except ValueError:
            cpu_value = None

        mem_part = (mem_raw or "").split("/")
        used_mb = _size_to_mb(mem_part[0].strip()) if mem_part else None
        limit_mb = _size_to_mb(mem_part[1].strip()) if len(mem_part) > 1 else None
        parsed[name] = {"cpu_percent": cpu_value, "memory_mb": used_mb, "memory_limit_mb": limit_mb}
    return parsed


def _directory_usage(path: Path) -> tuple[float, int]:
    total_bytes = 0
    inode_count = 0
    if not path.exists():
        return 0.0, 0
    for root, dirs, files in os.walk(path):
        inode_count += len(dirs) + len(files)
        for filename in files:
            target = Path(root) / filename
            try:
                total_bytes += target.stat().st_size
            except OSError:
                continue
    return total_bytes / (1024 * 1024), inode_count


def collect_client_resource_usage() -> list[dict]:
    stats = _docker_stats()
    client_home_root = Path(current_app.config.get("CLIENT_HOME_ROOT", "storage/clients"))

    payload: list[dict] = []
    for client in Client.query.order_by(Client.id.asc()).all():
        username = client.user.username if client.user else f"client-{client.id}"
        home_path = client_home_root / username
        disk_mb, inode_count = _directory_usage(home_path)
        container_name = client_apache_container_name(client)
        container_stats = stats.get(container_name, {})

        payload.append(
            {
                "client_id": client.id,
                "username": username,
                "container": container_name,
                "cpu_percent": container_stats.get("cpu_percent"),
                "memory_mb": container_stats.get("memory_mb"),
                "memory_limit_mb": container_stats.get("memory_limit_mb"),
                "disk_mb": round(disk_mb, 2),
                "inode_count": inode_count,
                "database_count": HostingDatabase.query.filter_by(client_id=client.id).count(),
                "mailbox_count": Mailbox.query.filter_by(client_id=client.id).count(),
            }
        )
    return payload


def record_client_resource_samples() -> int:
    samples = collect_client_resource_usage()
    for item in samples:
        db.session.add(
            ClientResourceSample(
                client_id=item["client_id"],
                cpu_percent=Decimal(str(item["cpu_percent"])) if item.get("cpu_percent") is not None else None,
                memory_mb=Decimal(str(item["memory_mb"])) if item.get("memory_mb") is not None else None,
                memory_limit_mb=Decimal(str(item["memory_limit_mb"])) if item.get("memory_limit_mb") is not None else None,
                disk_mb=Decimal(str(item["disk_mb"])) if item.get("disk_mb") is not None else None,
                inode_count=item.get("inode_count"),
                database_count=item.get("database_count"),
                mailbox_count=item.get("mailbox_count"),
                metadata_json={"container": item.get("container")},
            )
        )
    if samples:
        from panel.services.resource_limits import evaluate_all_clients_resource_alerts

        evaluate_all_clients_resource_alerts()
        db.session.commit()
    return len(samples)
