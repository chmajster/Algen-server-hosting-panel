from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from flask import current_app
from sqlalchemy import text

from panel.extensions import db
from panel.models import Client
from panel.services.client_apache import client_apache_container_name, client_apache_http_port, client_apache_prefix


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


def _run_docker(args: list[str], *, timeout: int = 8) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _client_has_active_domains(client: Client) -> bool:
    return any(domain.status != "disabled" for domain in client.domains)


def _collect_client_apache_checks() -> list[SmokeCheck]:
    checks: list[SmokeCheck] = []
    runtime_check = _check_client_apache_runtime()
    checks.append(runtime_check)
    if not current_app.config.get("CLIENT_APACHE_ENABLED", False) or not runtime_check.success:
        return checks

    bind_address = str(current_app.config.get("CLIENT_APACHE_BIND_ADDRESS", "127.0.0.1"))
    prefix = client_apache_prefix()

    list_result = _run_docker(["ps", "-a", "--format", "{{.Names}}"])
    if list_result.returncode != 0:
        message = list_result.stderr.strip() or list_result.stdout.strip() or "Nieznany blad docker ps."
        checks.append(SmokeCheck("Client Apache Containers", False, f"Nie mozna odczytac kontenerow: {message}"))
        return checks

    all_names = [line.strip() for line in list_result.stdout.splitlines() if line.strip()]
    managed_names = {name for name in all_names if name.startswith(f"{prefix}-")}

    expected: dict[str, dict] = {}
    for client in Client.query.order_by(Client.id.asc()).all():
        if _client_has_active_domains(client):
            expected[client_apache_container_name(client)] = {
                "client_id": client.id,
                "expected_port": client_apache_http_port(client),
            }

    expected_names = set(expected)
    missing = sorted(expected_names - managed_names)
    unexpected = sorted(managed_names - expected_names)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append(f"brakuje: {', '.join(missing)}")
        if unexpected:
            details.append(f"nadmiarowe: {', '.join(unexpected)}")
        checks.append(SmokeCheck("Client Apache Containers", False, "; ".join(details)))
    else:
        checks.append(SmokeCheck("Client Apache Containers", True, f"Kontenery sa zgodne z konfiguracja klientow ({len(expected_names)})."))

    inspect_failures: list[str] = []
    not_running: list[str] = []
    port_issues: list[str] = []
    collision_map: dict[int, str] = {}
    config_failures: list[str] = []

    for container_name in sorted(expected_names & managed_names):
        inspect_result = _run_docker(["inspect", container_name])
        if inspect_result.returncode != 0:
            message = inspect_result.stderr.strip() or inspect_result.stdout.strip() or "docker inspect fail"
            inspect_failures.append(f"{container_name}: {message}")
            continue
        try:
            payload = json.loads(inspect_result.stdout)
            container_data = payload[0]
        except (json.JSONDecodeError, IndexError, TypeError):
            inspect_failures.append(f"{container_name}: nieprawidlowy JSON z docker inspect")
            continue

        state = str(container_data.get("State", {}).get("Status", "unknown"))
        if state != "running":
            not_running.append(f"{container_name}={state}")
            continue

        bindings = container_data.get("NetworkSettings", {}).get("Ports", {}).get("80/tcp") or []
        if not bindings:
            port_issues.append(f"{container_name}: brak mapowania portu 80/tcp")
            continue

        binding = bindings[0]
        host_ip = str(binding.get("HostIp", ""))
        host_port_raw = str(binding.get("HostPort", "0"))
        expected_port = int(expected[container_name]["expected_port"])

        try:
            host_port = int(host_port_raw)
        except ValueError:
            port_issues.append(f"{container_name}: nieprawidlowy host port={host_port_raw}")
            continue

        if host_port != expected_port:
            port_issues.append(f"{container_name}: host port {host_port}, oczekiwano {expected_port}")
        if bind_address not in {"0.0.0.0", "::"} and host_ip not in {bind_address, "0.0.0.0", "::"}:
            port_issues.append(f"{container_name}: host ip {host_ip}, oczekiwano {bind_address}")
        if host_port in collision_map and collision_map[host_port] != container_name:
            port_issues.append(f"kolizja portu {host_port}: {collision_map[host_port]} oraz {container_name}")
        collision_map[host_port] = container_name

        config_result = _run_docker(["exec", container_name, "httpd", "-t"])
        if config_result.returncode != 0:
            message = config_result.stderr.strip() or config_result.stdout.strip() or "httpd -t fail"
            config_failures.append(f"{container_name}: {message}")

    if inspect_failures:
        checks.append(SmokeCheck("Client Apache Inspect", False, "; ".join(inspect_failures)))
    else:
        checks.append(SmokeCheck("Client Apache Inspect", True, "docker inspect dla kontenerow klientow zakonczony sukcesem."))

    if not_running:
        checks.append(SmokeCheck("Client Apache Runtime State", False, "; ".join(not_running)))
    else:
        checks.append(SmokeCheck("Client Apache Runtime State", True, "Wszystkie wymagane kontenery klientow sa uruchomione."))

    if port_issues:
        checks.append(SmokeCheck("Client Apache Port Mapping", False, "; ".join(port_issues)))
    else:
        checks.append(SmokeCheck("Client Apache Port Mapping", True, "Mapowanie portow i adresow jest poprawne."))

    if config_failures:
        checks.append(SmokeCheck("Client Apache VHost Config", False, "; ".join(config_failures)))
    else:
        checks.append(SmokeCheck("Client Apache VHost Config", True, "Konfiguracja Apache przechodzi walidacje httpd -t."))

    return checks


def write_smoke_test_log(result: SmokeResult, *, source: str) -> str | None:
    raw_path = str(current_app.config.get("SMOKE_TEST_LOG_FILE", "/var/log/hosting-panel/smoke-test.log")).strip()
    log_path = Path(raw_path or "/var/log/hosting-panel/smoke-test.log")
    payload = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": source,
        **result.as_dict(),
    }
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
        os.chmod(log_path, 0o640)
        if hasattr(os, "chown") and hasattr(os, "getuid") and hasattr(os, "getgid"):
            os.chown(log_path, os.getuid(), os.getgid())
    except OSError as exc:
        return str(exc)
    return None


def run_app_smoke_test() -> SmokeResult:
    started = time.perf_counter()
    checks = [
        _check_database(),
        _check_required_endpoints(),
        *_check_storage_paths(),
        *_collect_client_apache_checks(),
    ]
    duration_ms = int((time.perf_counter() - started) * 1000)
    return SmokeResult(checks=checks, duration_ms=duration_ms)
