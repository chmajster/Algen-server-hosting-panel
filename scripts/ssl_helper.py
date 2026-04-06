#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9.-]+(?<!-)$")


def validate_hostname(hostname: str) -> None:
    if not HOSTNAME_RE.match(hostname):
        raise ValueError("Nieprawidłowy hostname.")


def ensure_path(path_value: str) -> Path:
    path = Path(path_value).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_certbot_issue(hostname: str, email: str, document_root: str) -> dict:
    webroot = ensure_path(document_root)
    command = [
        "/usr/bin/certbot",
        "certonly",
        "--non-interactive",
        "--agree-tos",
        "--webroot",
        "-w",
        str(webroot),
        "-d",
        hostname,
        "-m",
        email,
        "--keep-until-expiring",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Certbot issue failed.")
    live_dir = Path("/etc/letsencrypt/live") / hostname
    return {
        "message": "Certyfikat wygenerowany przez certbot.",
        "certificate_path": str(live_dir / "fullchain.pem"),
        "private_key_path": str(live_dir / "privkey.pem"),
        "stdout": result.stdout.strip(),
    }


def run_certbot_renew(hostname: str) -> dict:
    command = ["/usr/bin/certbot", "renew", "--cert-name", hostname, "--non-interactive"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Certbot renew failed.")
    live_dir = Path("/etc/letsencrypt/live") / hostname
    return {
        "message": "Certyfikat odnowiony przez certbot.",
        "certificate_path": str(live_dir / "fullchain.pem"),
        "private_key_path": str(live_dir / "privkey.pem"),
        "stdout": result.stdout.strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()
    payload = json.loads(args.payload)
    action = payload.get("action")
    hostname = payload.get("hostname", "").strip().lower()
    validate_hostname(hostname)

    try:
        if action == "issue":
            email = payload.get("email", "").strip()
            document_root = payload.get("document_root", "").strip()
            if not email or not document_root:
                raise ValueError("Email i document_root są wymagane.")
            result = run_certbot_issue(hostname, email, document_root)
        elif action == "renew":
            result = run_certbot_renew(hostname)
        else:
            raise ValueError("Nieobsługiwana akcja SSL.")
        print(json.dumps(result))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
