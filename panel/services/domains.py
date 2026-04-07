from __future__ import annotations

import html
import json
import re
from pathlib import Path

from flask import current_app

from panel.models import Client, Domain


SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)
HOST_LABEL_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
PHP_VERSION_RE = re.compile(r"^[0-9]{1,2}(?:\.[0-9]{1,2}){0,2}$")


class DomainProvisioningError(RuntimeError):
    pass


def _normalize_hostname(value: str) -> str:
    normalized = (value or "").strip().lower()
    if HOSTNAME_RE.fullmatch(normalized) is None:
        raise DomainProvisioningError("Nieprawidlowa nazwa domeny.")
    return normalized


def _normalize_subdomain(value: str) -> str:
    normalized = (value or "").strip().lower()
    if HOST_LABEL_RE.fullmatch(normalized) is None:
        raise DomainProvisioningError("Nieprawidlowa nazwa subdomeny.")
    return normalized


def _normalize_php_version(value: str) -> str:
    normalized = (value or "").strip()
    if PHP_VERSION_RE.fullmatch(normalized) is None:
        raise DomainProvisioningError("Nieprawidlowa wersja PHP.")
    return normalized


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = SAFE_SEGMENT_RE.sub("-", (value or "").strip().lower()).strip(".-")
    return cleaned or fallback


def client_home_root(client: Client) -> Path:
    base_root = Path(current_app.config["CLIENT_HOME_ROOT"]).resolve()
    username = getattr(client.user, "username", "") if client.user is not None else ""
    home_root = base_root / _safe_segment(username, f"client-{client.id}")
    home_root.mkdir(parents=True, exist_ok=True)
    return home_root


def managed_domain_root(client: Client, domain_name: str) -> Path:
    return client_home_root(client) / "domains" / _safe_segment(domain_name, f"domain-{client.id}")


def managed_domain_public_root(client: Client, domain_name: str) -> Path:
    return managed_domain_root(client, domain_name) / "public"


def managed_subdomain_root(domain: Domain, subdomain_name: str) -> Path:
    return managed_domain_root(domain.client, domain.name) / "subdomains" / _safe_segment(subdomain_name, "subdomain")


def managed_subdomain_public_root(domain: Domain, subdomain_name: str) -> Path:
    return managed_subdomain_root(domain, subdomain_name) / "public"


def _default_htaccess() -> str:
    return "\n".join(
        [
            "Options -Indexes",
            "DirectoryIndex index.php index.html",
            "",
            "<IfModule mod_rewrite.c>",
            "    RewriteEngine On",
            "    RewriteCond %{REQUEST_FILENAME} !-f",
            "    RewriteCond %{REQUEST_FILENAME} !-d",
            "    RewriteRule ^ index.php [L]",
            "</IfModule>",
            "",
        ]
    )


def _default_index_html(hostname: str) -> str:
    safe_hostname = html.escape(hostname, quote=True)
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="pl">',
            "<head>",
            '  <meta charset="utf-8">',
            f"  <title>{safe_hostname}</title>",
            '  <meta name="viewport" content="width=device-width, initial-scale=1">',
            "</head>",
            "<body>",
            f"  <h1>{safe_hostname}</h1>",
            "  <p>Witryna zostala przygotowana przez Hosting Panel.</p>",
            "</body>",
            "</html>",
            "",
        ]
    )


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def provision_domain_tree(client: Client, domain_name: str, php_version: str) -> dict[str, str]:
    if client.user is None:
        raise DomainProvisioningError("Klient nie ma przypisanego uzytkownika.")

    domain_name = _normalize_hostname(domain_name)
    php_version = _normalize_php_version(php_version)

    domain_root = managed_domain_root(client, domain_name)
    public_root = domain_root / "public"
    private_root = domain_root / "private"
    subdomains_root = domain_root / "subdomains"
    ssl_root = domain_root / "ssl"
    config_root = domain_root / "config"

    for directory in (domain_root, public_root, private_root, subdomains_root, ssl_root, config_root):
        directory.mkdir(parents=True, exist_ok=True)

    htaccess_content = _default_htaccess()
    _write_if_missing(public_root / ".htaccess", htaccess_content)
    _write_if_missing(config_root / "default.htaccess", htaccess_content)
    _write_if_missing(public_root / "index.html", _default_index_html(domain_name))
    _write_if_missing(private_root / ".gitkeep", "")
    _write_if_missing(subdomains_root / ".gitkeep", "")
    _write_if_missing(ssl_root / ".gitkeep", "")

    _write_json(
        config_root / "domain.json",
        {
            "domain": domain_name,
            "php_version": php_version,
            "document_root": str(public_root),
            "private_root": str(private_root),
            "subdomains_root": str(subdomains_root),
            "ssl_root": str(ssl_root),
            "managed_by": "hosting-panel",
        },
    )
    (config_root / "php-version.conf").write_text(f"PHP_VERSION={php_version}\n", encoding="utf-8")
    (config_root / "paths.env").write_text(
        "\n".join(
            [
                f"DOMAIN_NAME={domain_name}",
                f"DOCUMENT_ROOT={public_root}",
                f"PRIVATE_ROOT={private_root}",
                f"SUBDOMAINS_ROOT={subdomains_root}",
                f"SSL_ROOT={ssl_root}",
                "",
            ]
        ),
        encoding="utf-8",
    )

    return {
        "home": str(client_home_root(client)),
        "domain": str(domain_root),
        "public": str(public_root),
        "private": str(private_root),
        "subdomains": str(subdomains_root),
        "ssl": str(ssl_root),
        "config": str(config_root),
    }


def provision_subdomain_tree(domain: Domain, subdomain_name: str, php_version: str) -> dict[str, str]:
    if domain.client is None:
        raise DomainProvisioningError("Domena nie ma przypisanego klienta.")

    subdomain_name = _normalize_subdomain(subdomain_name)
    php_version = _normalize_php_version(php_version)

    provision_domain_tree(domain.client, domain.name, domain.php_version)

    subdomain_root = managed_subdomain_root(domain, subdomain_name)
    public_root = subdomain_root / "public"
    private_root = subdomain_root / "private"
    ssl_root = subdomain_root / "ssl"
    config_root = subdomain_root / "config"

    for directory in (subdomain_root, public_root, private_root, ssl_root, config_root):
        directory.mkdir(parents=True, exist_ok=True)

    full_name = f"{_safe_segment(subdomain_name, 'subdomain')}.{domain.name}"
    htaccess_content = _default_htaccess()
    _write_if_missing(public_root / ".htaccess", htaccess_content)
    _write_if_missing(config_root / "default.htaccess", htaccess_content)
    _write_if_missing(public_root / "index.html", _default_index_html(full_name))
    _write_if_missing(private_root / ".gitkeep", "")
    _write_if_missing(ssl_root / ".gitkeep", "")

    _write_json(
        config_root / "subdomain.json",
        {
            "subdomain": subdomain_name,
            "full_name": full_name,
            "php_version": php_version,
            "document_root": str(public_root),
            "private_root": str(private_root),
            "ssl_root": str(ssl_root),
            "managed_by": "hosting-panel",
        },
    )
    (config_root / "php-version.conf").write_text(f"PHP_VERSION={php_version}\n", encoding="utf-8")

    return {
        "subdomain": str(subdomain_root),
        "public": str(public_root),
        "private": str(private_root),
        "ssl": str(ssl_root),
        "config": str(config_root),
    }
