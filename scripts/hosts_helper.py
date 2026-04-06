#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path


HOSTS_FILE = Path(os.environ.get("HOSTS_ALLOWED_FILE", "/etc/hosts"))
BACKUP_DIR = Path(os.environ.get("HOSTS_BACKUP_DIR", "/var/backups/hosting-panel/hosts"))
CRITICAL_HOSTS = {"localhost", "ip6-localhost", "ip6-loopback"}
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9.-]+(?<!-)$")


def validate_ip(value: str) -> None:
    ipaddress.ip_address(value)


def validate_hostname(value: str) -> None:
    if not HOSTNAME_RE.match(value):
        raise ValueError("Nieprawidłowy hostname.")


def parse_hosts() -> tuple[list[str], list[dict]]:
    lines = HOSTS_FILE.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) < 2:
            continue
        entries.append({"ip": parts[0], "hostnames": parts[1:]})
    return lines, entries


def create_backup() -> tuple[str, str, str]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup_name = f"hosts-{timestamp}.bak"
    backup_path = BACKUP_DIR / backup_name
    shutil.copy2(HOSTS_FILE, backup_path)
    checksum = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    return backup_name, str(backup_path), checksum


def write_hosts(lines: list[str]) -> None:
    temp_path = HOSTS_FILE.with_suffix(".tmp")
    temp_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    temp_path.replace(HOSTS_FILE)


def ensure_no_duplicate(entries: list[dict], hostname: str, ip_address: str, *, allow_same: bool = False) -> None:
    for entry in entries:
        if hostname in entry["hostnames"]:
            if allow_same and entry["ip"] == ip_address:
                return
            raise ValueError("Duplikat hosta w pliku hosts.")


def apply_add(ip_address: str, hostname: str) -> str:
    lines, entries = parse_hosts()
    ensure_no_duplicate(entries, hostname, ip_address)
    lines.append(f"{ip_address}\t{hostname}")
    write_hosts(lines)
    return "Dodano wpis."


def apply_update(ip_address: str, hostname: str, previous_value: str | None) -> str:
    lines, entries = parse_hosts()
    updated = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.split()
        if len(parts) >= 2 and hostname in parts[1:]:
            if previous_value and parts[0] != previous_value:
                continue
            parts[0] = ip_address
            lines[index] = "\t".join([parts[0], *parts[1:]])
            updated = True
            break
    if not updated:
        raise ValueError("Nie znaleziono wpisu do aktualizacji.")
    write_hosts(lines)
    return "Zaktualizowano wpis."


def apply_delete(hostname: str, force_critical: bool) -> str:
    if hostname in CRITICAL_HOSTS and not force_critical:
        raise ValueError("Próba usunięcia krytycznego wpisu wymaga potwierdzenia.")
    lines, _ = parse_hosts()
    new_lines = []
    deleted = False
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            new_lines.append(line)
            continue
        parts = stripped.split()
        if len(parts) >= 2 and hostname in parts[1:]:
            remaining = [item for item in parts[1:] if item != hostname]
            if remaining:
                new_lines.append("\t".join([parts[0], *remaining]))
            deleted = True
        else:
            new_lines.append(line)
    if not deleted:
        raise ValueError("Nie znaleziono wpisu do usunięcia.")
    write_hosts(new_lines)
    return "Usunięto wpis."


def restore_backup(backup_name: str) -> str:
    backup_path = BACKUP_DIR / backup_name
    if not backup_path.exists():
        raise ValueError("Backup nie istnieje.")
    shutil.copy2(backup_path, HOSTS_FILE)
    return "Przywrócono backup."


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()
    payload = json.loads(args.payload)
    action = payload.get("action")

    if action == "list":
        _, entries = parse_hosts()
        print(json.dumps({"entries": entries}))
        return 0

    backup_name, backup_path, checksum = create_backup()
    try:
        if action in {"add", "update"}:
            ip_address = payload.get("ip_address", "").strip()
            hostname = payload.get("hostname", "").strip().lower()
            if not ip_address or not hostname:
                raise ValueError("IP i hostname są wymagane.")
            validate_ip(ip_address)
            validate_hostname(hostname)
            if action == "add":
                message = apply_add(ip_address, hostname)
            else:
                message = apply_update(ip_address, hostname, payload.get("previous_value"))
        elif action == "delete":
            hostname = payload.get("hostname", "").strip().lower()
            if not hostname:
                raise ValueError("Hostname jest wymagany.")
            validate_hostname(hostname)
            message = apply_delete(hostname, bool(payload.get("force_critical")))
        elif action == "restore":
            message = restore_backup(payload.get("backup_name", ""))
        else:
            raise ValueError("Nieobsługiwana akcja.")
        print(
            json.dumps(
                {
                    "backup_name": backup_name,
                    "backup_path": backup_path,
                    "checksum": checksum,
                    "message": message,
                }
            )
        )
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
