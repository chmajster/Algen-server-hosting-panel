from __future__ import annotations

import ipaddress


def _normalize_allowlist(allowlist: str | list[str] | tuple[str, ...] | None) -> list[str]:
    if allowlist is None:
        return []
    if isinstance(allowlist, str):
        return [item.strip() for item in allowlist.split(",") if item.strip()]
    values: list[str] = []
    for item in allowlist:
        normalized = str(item).strip()
        if normalized:
            values.append(normalized)
    return values


def is_ip_allowed(ip_value: str | None, allowlist: str | list[str] | tuple[str, ...] | None, *, default_allow: bool = False) -> bool:
    entries = _normalize_allowlist(allowlist)
    if not entries:
        return default_allow

    ip_text = (ip_value or "").strip()
    if not ip_text:
        return False

    try:
        client_ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return False

    for entry in entries:
        if entry in {"*", "any", "all"}:
            return True
        try:
            network = ipaddress.ip_network(entry, strict=False)
        except ValueError:
            continue
        if client_ip in network:
            return True

    return False
