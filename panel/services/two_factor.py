from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from string import digits
from urllib.parse import quote


TOTP_DIGITS = 6
TOTP_INTERVAL_SECONDS = 30


def generate_two_factor_secret() -> str:
    # 20 random bytes gives a compact and standard 32-char Base32 secret.
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def normalize_totp_code(value: str | None) -> str:
    code = "".join(ch for ch in (value or "") if ch.isdigit())
    if len(code) != TOTP_DIGITS:
        return ""
    return code


def generate_email_code(length: int = 6) -> str:
    size = max(length, 4)
    return "".join(secrets.choice(digits) for _ in range(size))


def build_email_code_hash(*, secret_key: str, user_id: int, code: str) -> str:
    normalized = normalize_totp_code(code)
    payload = f"{user_id}:{normalized}".encode("utf-8")
    return hmac.new(secret_key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _decode_base32_secret(secret: str) -> bytes:
    normalized = "".join((secret or "").strip().upper().split())
    if not normalized:
        raise ValueError("Brak sekretu 2FA.")
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    return base64.b32decode(normalized + padding, casefold=True)


def _hotp(secret: str, counter: int) -> str:
    key = _decode_base32_secret(secret)
    counter_bytes = struct.pack(">Q", max(counter, 0))
    digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary_code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    value = binary_code % (10**TOTP_DIGITS)
    return f"{value:0{TOTP_DIGITS}d}"


def current_totp(secret: str, now: float | None = None) -> str:
    timestamp = time.time() if now is None else now
    counter = int(timestamp // TOTP_INTERVAL_SECONDS)
    return _hotp(secret, counter)


def verify_totp_code(secret: str, code: str | None, *, valid_window: int = 1, now: float | None = None) -> bool:
    normalized_code = normalize_totp_code(code)
    if not normalized_code:
        return False
    timestamp = time.time() if now is None else now
    counter = int(timestamp // TOTP_INTERVAL_SECONDS)
    for delta in range(-valid_window, valid_window + 1):
        if hmac.compare_digest(_hotp(secret, counter + delta), normalized_code):
            return True
    return False


def build_totp_uri(*, secret: str, username: str, issuer: str) -> str:
    label = quote(f"{issuer}:{username}")
    issuer_param = quote(issuer)
    return (
        f"otpauth://totp/{label}"
        f"?secret={secret}&issuer={issuer_param}&digits={TOTP_DIGITS}&period={TOTP_INTERVAL_SECONDS}"
    )


def format_secret_for_display(secret: str) -> str:
    value = "".join((secret or "").strip().upper().split())
    return " ".join(value[i : i + 4] for i in range(0, len(value), 4))
