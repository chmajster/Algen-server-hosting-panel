from __future__ import annotations

from datetime import datetime, timedelta

from flask import current_app

from panel.extensions import db
from panel.models import Domain, SSLCertificate, Subdomain
from panel.services.audit import log_activity
from panel.services.hosts import HostsHelperError

import json
import subprocess


class SSLServiceError(RuntimeError):
    pass


def parse_target_ref(target_ref: str):
    try:
        target_type, target_id = target_ref.split(":", 1)
        return target_type, int(target_id)
    except (ValueError, TypeError):
        raise SSLServiceError("Nieprawidłowy identyfikator witryny.")


def resolve_ssl_target(target_ref: str):
    target_type, target_id = parse_target_ref(target_ref)
    if target_type == "domain":
        target = Domain.query.get_or_404(target_id)
        return target_type, target, target.name, target.client
    if target_type == "subdomain":
        target = Subdomain.query.get_or_404(target_id)
        return target_type, target, target.full_name, target.domain.client
    raise SSLServiceError("Nieobsługiwany typ witryny.")


def run_ssl_helper(payload: dict) -> dict:
    command = [
        current_app.config["HOSTS_SUDO_BIN"],
        current_app.config["SSL_HELPER_PATH"],
        "--payload",
        json.dumps(payload),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SSLServiceError(result.stderr.strip() or "Helper SSL zakończył się błędem.")
    return json.loads(result.stdout or "{}")


def bind_certificate_target(cert: SSLCertificate, target_type: str, target) -> None:
    cert.domain = None
    cert.subdomain = None
    if target_type == "domain":
        cert.domain = target
        target.ssl_enabled = True
    elif target_type == "subdomain":
        cert.subdomain = target
        target.ssl_enabled = True


def issue_certificate(cert: SSLCertificate, actor=None) -> SSLCertificate:
    if cert.provider != "letsencrypt":
        raise SSLServiceError("Automatyczne generowanie jest dostępne tylko dla providerów Let's Encrypt.")
    target_name = cert.target_name
    document_root = cert.domain.document_root if cert.domain is not None else cert.subdomain.document_root
    result = run_ssl_helper(
        {
            "action": "issue",
            "hostname": target_name,
            "provider": cert.provider,
            "document_root": document_root,
            "email": current_app.config["LETSENCRYPT_EMAIL"],
        }
    )
    cert.status = "active"
    cert.valid_from = datetime.utcnow()
    cert.valid_until = datetime.utcnow() + timedelta(days=90)
    cert.certificate_path = result.get("certificate_path", cert.certificate_path)
    cert.private_key_path = result.get("private_key_path", cert.private_key_path)
    cert.metadata_json = {
        **(cert.metadata_json or {}),
        "last_issue_result": result,
    }
    log_activity(
        "ssl.issue",
        "ssl_certificate",
        f"Wygenerowano certyfikat SSL dla {target_name}",
        entity_id=cert.id,
        client=cert.domain.client if cert.domain is not None else cert.subdomain.domain.client,
        actor=actor,
    )
    db.session.add(cert)
    return cert


def renew_certificate(cert: SSLCertificate, actor=None) -> SSLCertificate:
    if cert.provider != "letsencrypt":
        raise SSLServiceError("Automatyczne odnawianie jest dostępne tylko dla providerów Let's Encrypt.")
    result = run_ssl_helper({"action": "renew", "hostname": cert.target_name, "provider": cert.provider})
    cert.status = "active"
    cert.valid_until = datetime.utcnow() + timedelta(days=90)
    cert.metadata_json = {**(cert.metadata_json or {}), "last_renew_result": result}
    log_activity(
        "ssl.renew",
        "ssl_certificate",
        f"Odnowiono certyfikat SSL dla {cert.target_name}",
        entity_id=cert.id,
        client=cert.domain.client if cert.domain is not None else cert.subdomain.domain.client,
        actor=actor,
    )
    db.session.add(cert)
    return cert
