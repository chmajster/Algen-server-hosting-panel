from __future__ import annotations

import platform
import shutil
import subprocess
from dataclasses import dataclass

import psutil


SERVICE_MAP = {
    "nginx": "nginx",
    "apache": "apache2",
    "mariadb": "mariadb",
    "php-fpm": "php8.3-fpm",
    "ssh": "ssh",
    "ftp": "vsftpd",
    "dns": "bind9",
    "mail": "postfix",
}


@dataclass
class MetricCard:
    label: str
    value: float
    unit: str


def collect_server_metrics() -> list[MetricCard]:
    disk = psutil.disk_usage("/")
    return [
        MetricCard("CPU", psutil.cpu_percent(interval=0.2), "%"),
        MetricCard("RAM", psutil.virtual_memory().percent, "%"),
        MetricCard("Dysk", disk.percent, "%"),
    ]


def service_statuses() -> dict[str, str]:
    statuses = {}
    if platform.system().lower() != "linux" or shutil.which("systemctl") is None:
        return {key: "unknown" for key in SERVICE_MAP}
    for label, unit in SERVICE_MAP.items():
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True,
            text=True,
            check=False,
        )
        statuses[label] = result.stdout.strip() or "unknown"
    return statuses
