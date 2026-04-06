from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from panel.extensions import db


@dataclass(slots=True)
class SmokeCheck:
    name: str
    success: bool
    message: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "success": self.success,
            "message": self.message,
        }


@dataclass(slots=True)
class SmokeResult:
    checks: list[SmokeCheck]
    duration_ms: int

    @property
    def passed(self) -> int:
        return sum(1 for item in self.checks if item.success)

    @property
    def failed(self) -> int:
        return sum(1 for item in self.checks if not item.success)

    @property
    def total(self) -> int:
        return len(self.checks)

    @property
    def success(self) -> bool:
        return self.failed == 0

    def as_dict(self) -> dict:
        return {
            "success": self.success,
            "passed": self.passed,
            "failed": self.failed,
            "total": self.total,
            "duration_ms": self.duration_ms,
            "checks": [item.as_dict() for item in self.checks],
        }


def _check_database() -> SmokeCheck:
    try:
        value = db.session.execute(text("SELECT 1")).scalar()
    except Exception as exc:
        return SmokeCheck("Database", False, f"Zapytanie testowe nie powiodlo sie: {exc}")
    if value != 1:
        return SmokeCheck("Database", False, "Zapytanie testowe zwrocilo nieoczekiwany wynik.")
    return SmokeCheck("Database", True, "Polaczenie z baza danych dziala.")


def _check_required_endpoints() -> SmokeCheck:
    required = {
        "auth.login",
        "admin.dashboard",
        "admin.users",
        "monitoring.index",
        "domains.admin_domains",
        "databases.admin_databases",
    }
    registered = {rule.endpoint for rule in current_app.url_map.iter_rules()}
    missing = sorted(required - registered)
    if missing:
        return SmokeCheck("Routes", False, f"Brak endpointow: {', '.join(missing)}")
    return SmokeCheck("Routes", True, "Kluczowe endpointy aplikacji sa zarejestrowane.")


def _check_storage_paths() -> list[SmokeCheck]:
    checks: list[SmokeCheck] = []
    for key in ["STORAGE_ROOT", "CLIENT_HOME_ROOT", "BACKUP_ROOT"]:
        raw_path = str(current_app.config.get(key, "")).strip()
        if not raw_path:
            checks.append(SmokeCheck(key, False, "Brak wartosci konfiguracji."))
            continue

        path = Path(raw_path)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            checks.append(SmokeCheck(key, False, f"Nie mozna utworzyc katalogu {path}: {exc}"))
            continue

        if not path.is_dir():
            checks.append(SmokeCheck(key, False, f"Sciezka {path} nie jest katalogiem."))
            continue

        if not os.access(path, os.W_OK):
            checks.append(SmokeCheck(key, False, f"Brak prawa zapisu do katalogu {path}."))
            continue

        checks.append(SmokeCheck(key, True, f"Katalog {path} jest dostepny i zapisywalny."))
    return checks


def _check_client_apache_runtime() -> SmokeCheck:
    if not current_app.config.get("CLIENT_APACHE_ENABLED", False):
        return SmokeCheck("Client Apache", True, "Funkcja CLIENT_APACHE_ENABLED jest wylaczona.")

    if shutil.which("docker") is None:
        return SmokeCheck("Client Apache", False, "Nie znaleziono polecenia docker.")

    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
    except Exception as exc:
        return SmokeCheck("Client Apache", False, f"Nie mozna sprawdzic dockera: {exc}")

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Nieznany blad docker info."
        return SmokeCheck("Client Apache", False, message)

    version = result.stdout.strip() or "unknown"
    return SmokeCheck("Client Apache", True, f"Docker odpowiada (ServerVersion={version}).")


def run_app_smoke_test() -> SmokeResult:
    started = time.perf_counter()
    checks = [
        _check_database(),
        _check_required_endpoints(),
        *_check_storage_paths(),
        _check_client_apache_runtime(),
    ]
    duration_ms = int((time.perf_counter() - started) * 1000)
    return SmokeResult(checks=checks, duration_ms=duration_ms)
