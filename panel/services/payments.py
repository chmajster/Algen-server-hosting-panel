from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from decimal import Decimal

from flask import current_app, url_for

from panel.models import Client, OnlinePayment


class PaymentProviderError(RuntimeError):
    pass


@dataclass
class CheckoutSessionData:
    provider: str
    checkout_url: str
    external_id: str | None = None


def is_online_payments_enabled() -> bool:
    return bool(current_app.config.get("ONLINE_PAYMENTS_ENABLED", False))


def online_payments_provider() -> str:
    value = (current_app.config.get("ONLINE_PAYMENTS_PROVIDER", "stripe") or "stripe").strip().lower()
    return value or "stripe"


def create_checkout_session(payment: OnlinePayment, client: Client) -> CheckoutSessionData:
    provider = online_payments_provider()
    if provider == "mock":
        checkout_url = url_for("billing.client_topup_mock_success", payment_id=payment.id, _external=True)
        return CheckoutSessionData(provider="mock", checkout_url=checkout_url, external_id=f"mock_{payment.id}")
    if provider == "stripe":
        return _create_stripe_checkout_session(payment, client)
    raise PaymentProviderError(f"Nieobslugiwany provider platnosci: {provider}")


def retrieve_checkout_session(external_id: str, provider: str | None = None) -> dict:
    selected = (provider or online_payments_provider()).strip().lower()
    if selected != "stripe":
        raise PaymentProviderError("Weryfikacja sesji jest dostepna tylko dla providera Stripe.")
    return _stripe_api_request("GET", f"/v1/checkout/sessions/{external_id}")


def parse_stripe_webhook_event(payload: bytes, signature_header: str) -> dict:
    webhook_secret = (current_app.config.get("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not webhook_secret:
        raise PaymentProviderError("Brak STRIPE_WEBHOOK_SECRET.")

    timestamp = None
    signatures: list[str] = []
    for chunk in (signature_header or "").split(","):
        if "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "t":
            try:
                timestamp = int(value)
            except ValueError as exc:
                raise PaymentProviderError("Nieprawidlowy znacznik czasu podpisu webhook.") from exc
        elif key == "v1":
            signatures.append(value)

    if timestamp is None or not signatures:
        raise PaymentProviderError("Brak danych podpisu webhook Stripe.")

    tolerance = int(current_app.config.get("STRIPE_WEBHOOK_TOLERANCE_SECONDS", 300))
    if abs(time.time() - timestamp) > tolerance:
        raise PaymentProviderError("Podpis webhook Stripe wygasl.")

    signed_payload = f"{timestamp}.{payload.decode('utf-8')}".encode("utf-8")
    expected_signature = hmac.new(
        webhook_secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(expected_signature, candidate) for candidate in signatures):
        raise PaymentProviderError("Nieprawidlowy podpis webhook Stripe.")

    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise PaymentProviderError("Nieprawidlowy payload webhook Stripe.") from exc


def is_paid_checkout_session(session_payload: dict) -> bool:
    payment_status = (session_payload.get("payment_status") or "").strip().lower()
    return payment_status in {"paid", "no_payment_required"}


def _create_stripe_checkout_session(payment: OnlinePayment, client: Client) -> CheckoutSessionData:
    secret_key = (current_app.config.get("STRIPE_SECRET_KEY") or "").strip()
    if not secret_key:
        raise PaymentProviderError("Brak STRIPE_SECRET_KEY.")

    success_url = _build_success_url()
    cancel_url = _build_cancel_url()
    amount_cents = int((Decimal(payment.amount) * 100).quantize(Decimal("1")))

    payload = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": str(client.id),
        "metadata[payment_id]": str(payment.id),
        "metadata[client_id]": str(client.id),
        "line_items[0][price_data][currency]": payment.currency.lower(),
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][price_data][product_data][name]": payment.description,
        "line_items[0][quantity]": "1",
    }
    session = _stripe_api_request("POST", "/v1/checkout/sessions", payload)
    checkout_url = (session.get("url") or "").strip()
    if not checkout_url:
        raise PaymentProviderError("Stripe nie zwrocil adresu sesji checkout.")

    external_id = (session.get("id") or "").strip() or None
    return CheckoutSessionData(provider="stripe", checkout_url=checkout_url, external_id=external_id)


def _build_success_url() -> str:
    configured = (current_app.config.get("ONLINE_PAYMENTS_SUCCESS_URL") or "").strip()
    if configured:
        return _ensure_checkout_placeholder(configured)
    base = url_for("billing.client_topup_return", _external=True)
    return _ensure_checkout_placeholder(base)


def _build_cancel_url() -> str:
    configured = (current_app.config.get("ONLINE_PAYMENTS_CANCEL_URL") or "").strip()
    if configured:
        return configured
    base = url_for("billing.client_topup_return", _external=True)
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}canceled=1"


def _ensure_checkout_placeholder(url: str) -> str:
    if "{CHECKOUT_SESSION_ID}" in url:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}checkout_session_id={{CHECKOUT_SESSION_ID}}"


def _stripe_api_request(method: str, path: str, payload: dict | None = None) -> dict:
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    secret_key = (current_app.config.get("STRIPE_SECRET_KEY") or "").strip()
    if not secret_key:
        raise PaymentProviderError("Brak STRIPE_SECRET_KEY.")

    base_url = "https://api.stripe.com"
    request_data = None
    headers = {
        "Authorization": f"Bearer {secret_key}",
    }
    if payload is not None:
        request_data = urlencode(payload).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    request = Request(base_url + path, data=request_data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        message = "Stripe zwrocil blad API."
        try:
            parsed = json.loads(raw)
            error_obj = parsed.get("error") or {}
            message = (error_obj.get("message") or message).strip()
        except json.JSONDecodeError:
            pass
        raise PaymentProviderError(message) from exc
    except URLError as exc:
        raise PaymentProviderError("Brak polaczenia z API Stripe.") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise PaymentProviderError("Nieprawidlowa odpowiedz API Stripe.") from exc
