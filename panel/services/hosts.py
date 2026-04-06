from __future__ import annotations

import json
import subprocess

from flask import current_app


class HostsHelperError(RuntimeError):
    pass


def run_hosts_helper(payload: dict) -> dict:
    command = [
        current_app.config["HOSTS_SUDO_BIN"],
        current_app.config["HOSTS_HELPER_PATH"],
        "--payload",
        json.dumps(payload),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise HostsHelperError(result.stderr.strip() or "Helper hosts zakończył się błędem.")
    return json.loads(result.stdout or "{}")
