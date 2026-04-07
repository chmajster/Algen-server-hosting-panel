from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath

from panel.models import Client
from panel.services.audit import log_activity
from panel.services.domains import client_home_root, managed_domain_public_root, managed_subdomain_public_root


SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


class ClientApacheServiceError(RuntimeError):
    pass


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = SAFE_SEGMENT_RE.sub("-", (value or "").strip().lower()).strip(".-")
    return cleaned or fallback


def _run_docker(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise ClientApacheServiceError("Nie znaleziono polecenia 'docker'.") from exc

    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "Nieznany blad Dockera."
        raise ClientApacheServiceError(f"Docker {' '.join(args)} zakonczyl sie bledem: {message}")
    return result


def _container_name(client: Client) -> str:
    username = getattr(client.user, "username", "") if client.user is not None else ""
    suffix = _safe_segment(username, f"client-{client.id}")
    return f"{client_apache_prefix()}-{suffix}-{client.id}"


def client_apache_prefix() -> str:
    from flask import current_app

    return current_app.config.get("CLIENT_APACHE_CONTAINER_PREFIX", "hosting-panel-client-apache")


def client_apache_container_name(client: Client) -> str:
    return _container_name(client)


def client_apache_http_port(client: Client) -> int:
    from flask import current_app

    http_port_base = int(current_app.config.get("CLIENT_APACHE_HTTP_PORT_BASE", 18000))
    return http_port_base + int(client.id)


def _domain_mount_root(client: Client) -> Path:
    return client_home_root(client) / "domains"


def _apache_config_root(client: Client) -> Path:
    root = client_home_root(client) / "docker" / "apache"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _host_path_to_container_docroot(client: Client, host_docroot: Path) -> str:
    domains_root = _domain_mount_root(client).resolve()
    container_root = PurePosixPath("/var/www/domains")

    try:
        relative = host_docroot.resolve().relative_to(domains_root)
        return str(container_root.joinpath(*relative.parts))
    except ValueError:
        return str(container_root)


def _domain_docroot(client: Client, domain) -> Path:
    if domain.document_root:
        return Path(domain.document_root)
    return managed_domain_public_root(client, domain.name)


def _subdomain_docroot(domain, subdomain) -> Path:
    if subdomain.document_root:
        return Path(subdomain.document_root)
    return managed_subdomain_public_root(domain, subdomain.name)


def _collect_virtual_hosts(client: Client) -> list[dict]:
    hosts: list[dict] = []
    domains = sorted(client.domains, key=lambda item: item.name)

    for domain in domains:
        if domain.status == "disabled":
            continue

        domain_root = _domain_docroot(client, domain)
        domain_root.mkdir(parents=True, exist_ok=True)
        hosts.append(
            {
                "server_name": domain.name,
                "aliases": [],
                "document_root": _host_path_to_container_docroot(client, domain_root),
            }
        )

        subdomains = sorted(domain.subdomains, key=lambda item: item.name)
        for subdomain in subdomains:
            if subdomain.status == "disabled":
                continue
            subdomain_root = _subdomain_docroot(domain, subdomain)
            subdomain_root.mkdir(parents=True, exist_ok=True)
            hosts.append(
                {
                    "server_name": subdomain.full_name,
                    "aliases": [],
                    "document_root": _host_path_to_container_docroot(client, subdomain_root),
                }
            )

    return hosts


def _render_vhost_config(client: Client, virtual_hosts: list[dict]) -> str:
    lines = [
        "ServerName localhost",
        "",
    ]

    if not virtual_hosts:
        lines.extend(
            [
                "<VirtualHost *:80>",
                f"    ServerName {client.user.username}.no-domains.local" if client.user is not None else f"    ServerName client-{client.id}.no-domains.local",
                "    DocumentRoot /var/www/domains",
                "    ErrorLog /proc/self/fd/2",
                "    CustomLog /proc/self/fd/1 common",
                "    <Directory /var/www/domains>",
                "        Options Indexes FollowSymLinks",
                "        AllowOverride All",
                "        Require all granted",
                "    </Directory>",
                "</VirtualHost>",
                "",
            ]
        )

    for host in virtual_hosts:
        lines.extend(
            [
                "<VirtualHost *:80>",
                f"    ServerName {host['server_name']}",
                *(f"    ServerAlias {alias}" for alias in host["aliases"]),
                f"    DocumentRoot {host['document_root']}",
                "    ErrorLog /proc/self/fd/2",
                "    CustomLog /proc/self/fd/1 common",
                f"    <Directory {host['document_root']}>",
                "        Options Indexes FollowSymLinks",
                "        AllowOverride All",
                "        Require all granted",
                "    </Directory>",
                "</VirtualHost>",
                "",
            ]
        )

    return "\n".join(lines)


def _docker_container_exists(name: str) -> bool:
    result = _run_docker(["container", "inspect", name], check=False)
    return result.returncode == 0


def _remove_container_if_exists(name: str) -> None:
    if _docker_container_exists(name):
        _run_docker(["rm", "-f", name])


def _ensure_container(name: str, client: Client, vhost_file: Path) -> None:
    from flask import current_app

    bind_address = current_app.config.get("CLIENT_APACHE_BIND_ADDRESS", "127.0.0.1")
    image = current_app.config.get("CLIENT_APACHE_IMAGE", "httpd:2.4")
    http_port = client_apache_http_port(client)

    domain_mount = _domain_mount_root(client)
    domain_mount.mkdir(parents=True, exist_ok=True)

    if not _docker_container_exists(name):
        _run_docker(
            [
                "run",
                "-d",
                "--name",
                name,
                "--restart",
                "unless-stopped",
                "-p",
                f"{bind_address}:{http_port}:80",
                "-v",
                f"{domain_mount}:/var/www/domains:rw",
                "-v",
                f"{vhost_file}:/usr/local/apache2/conf/extra/httpd-vhosts.conf:ro",
                image,
                "httpd-foreground",
                "-C",
                "Include /usr/local/apache2/conf/extra/httpd-vhosts.conf",
            ]
        )
    else:
        _run_docker(["start", name], check=False)

    graceful_reload = _run_docker(["exec", name, "httpd", "-k", "graceful"], check=False)
    if graceful_reload.returncode != 0:
        _run_docker(["restart", name])


def sync_client_apache_instance(client: Client, *, reason: str, actor=None) -> dict:
    from flask import current_app

    if not current_app.config.get("CLIENT_APACHE_ENABLED", False):
        return {"enabled": False, "reason": "disabled"}

    virtual_hosts = _collect_virtual_hosts(client)
    container_name = _container_name(client)
    remove_empty = bool(current_app.config.get("CLIENT_APACHE_REMOVE_EMPTY", True))

    if not virtual_hosts and remove_empty:
        _remove_container_if_exists(container_name)
        payload = {
            "enabled": True,
            "container": container_name,
            "status": "removed",
            "vhosts": 0,
            "reason": reason,
        }
        log_activity(
            "domains.apache_sync",
            "client_apache",
            f"Usunieto kontener Apache klienta {client.user.username if client.user else client.id} (brak vhostow, trigger: {reason})",
            entity_id=client.id,
            client=client,
            actor=actor,
            metadata=payload,
        )
        return payload

    config_root = _apache_config_root(client)
    vhost_file = config_root / "httpd-vhosts.conf"
    vhost_file.write_text(_render_vhost_config(client, virtual_hosts), encoding="utf-8")

    _ensure_container(container_name, client, vhost_file)

    payload = {
        "enabled": True,
        "container": container_name,
        "status": "running",
        "vhosts": len(virtual_hosts),
        "reason": reason,
    }
    log_activity(
        "domains.apache_sync",
        "client_apache",
        f"Zsynchronizowano kontener Apache klienta {client.user.username if client.user else client.id} (trigger: {reason})",
        entity_id=client.id,
        client=client,
        actor=actor,
        metadata=payload,
    )
    return payload
