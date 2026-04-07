from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from flask import current_app

from panel.extensions import db
from panel.models import RegistrationFraudCheck, User


SUSPICIOUS_USERNAME_TOKENS = (
    "test",
    "temp",
    "spam",
    "bot",
    "fake",
    "throwaway",
    "demo",
)
BOT_USER_AGENT_TOKENS = (
    "python-requests",
    "curl/",
    "wget/",
    "scrapy",
    "httpclient",
    "selenium",
)


@dataclass(frozen=True)
class FraudAssessment:
    score: int
    risk_level: str
    blocked: bool
    requires_review: bool
    reasons: list[str]


def _config_int(key: str, fallback: int) -> int:
    try:
        value = int(current_app.config.get(key, fallback))
    except (TypeError, ValueError):
        value = fallback
    return max(0, value)


def _clamp_score(value: int) -> int:
    return max(0, min(int(value), 100))


def _disposable_domains() -> set[str]:
    configured = str(current_app.config.get("ANTI_FRAUD_DISPOSABLE_DOMAINS", "") or "")
    domains = {
        item.strip().lower()
        for item in configured.split(",")
        if item and item.strip()
    }
    return domains


def _recent_ip_registrations(ip_address: str | None) -> int:
    normalized_ip = (ip_address or "").strip()
    if not normalized_ip:
        return 0
    window_minutes = max(1, _config_int("ANTI_FRAUD_IP_WINDOW_MINUTES", 30))
    window_start = datetime.utcnow() - timedelta(minutes=window_minutes)
    return (
        RegistrationFraudCheck.query.filter_by(ip_address=normalized_ip)
        .filter(RegistrationFraudCheck.created_at >= window_start)
        .count()
    )


def assess_registration_risk(
    *,
    username: str,
    email: str,
    first_name: str,
    last_name: str,
    ip_address: str | None,
    user_agent: str | None,
) -> FraudAssessment:
    if not bool(current_app.config.get("ANTI_FRAUD_ENABLED", True)):
        return FraudAssessment(score=0, risk_level="low", blocked=False, requires_review=False, reasons=[])

    raw_username = (username or "").strip().lower()
    raw_email = (email or "").strip().lower()
    raw_first_name = (first_name or "").strip().lower()
    raw_last_name = (last_name or "").strip().lower()
    raw_agent = (user_agent or "").strip().lower()

    score = 0
    reasons: list[str] = []

    _, _, email_domain = raw_email.partition("@")
    if email_domain in _disposable_domains():
        score += 60
        reasons.append("Adres e-mail nalezy do domeny tymczasowej.")
    if "." not in email_domain:
        score += 15
        reasons.append("Adres e-mail ma nietypowa domene.")

    if raw_username and raw_username.isdigit():
        score += 20
        reasons.append("Login sklada sie tylko z cyfr.")
    elif raw_username:
        digits = sum(1 for char in raw_username if char.isdigit())
        if digits / len(raw_username) >= 0.5:
            score += 15
            reasons.append("Login zawiera duzo cyfr.")

    if any(token in raw_username for token in SUSPICIOUS_USERNAME_TOKENS):
        score += 20
        reasons.append("Login zawiera wzorce zwiazane z kontami jednorazowymi.")

    if len(raw_first_name) < 2 or len(raw_last_name) < 2:
        score += 10
        reasons.append("Imie lub nazwisko jest bardzo krotkie.")
    if raw_first_name and raw_first_name == raw_last_name:
        score += 10
        reasons.append("Imie i nazwisko sa identyczne.")

    if any(token in raw_agent for token in BOT_USER_AGENT_TOKENS):
        score += 25
        reasons.append("User-Agent wyglada na automatyczny klient.")

    recent_ip = _recent_ip_registrations(ip_address)
    ip_threshold = max(1, _config_int("ANTI_FRAUD_IP_THRESHOLD", 3))
    attempt_number = recent_ip + 1
    if attempt_number > ip_threshold:
        velocity_score = min(70, 30 + (attempt_number - ip_threshold) * 10)
        score += velocity_score
        reasons.append(
            f"Wysoka liczba rejestracji z IP ({attempt_number} prob w oknie czasowym)."
        )

    review_threshold = _config_int("ANTI_FRAUD_REVIEW_THRESHOLD", 50)
    block_threshold = max(review_threshold + 1, _config_int("ANTI_FRAUD_BLOCK_THRESHOLD", 80))
    score = _clamp_score(score)

    if score >= block_threshold:
        return FraudAssessment(score=score, risk_level="high", blocked=True, requires_review=True, reasons=reasons)
    if score >= review_threshold:
        return FraudAssessment(score=score, risk_level="medium", blocked=False, requires_review=True, reasons=reasons)
    return FraudAssessment(score=score, risk_level="low", blocked=False, requires_review=False, reasons=reasons)


def create_registration_fraud_check(
    *,
    user: User | None,
    username: str,
    email: str,
    ip_address: str | None,
    user_agent: str | None,
    assessment: FraudAssessment,
    metadata: dict | None = None,
) -> RegistrationFraudCheck:
    row = RegistrationFraudCheck(
        user=user,
        username=(username or "").strip(),
        email=(email or "").strip().lower(),
        ip_address=(ip_address or "").strip() or None,
        user_agent=(user_agent or "").strip() or None,
        score=int(assessment.score),
        risk_level=assessment.risk_level,
        blocked=bool(assessment.blocked),
        reasons_json=list(assessment.reasons),
        metadata_json=metadata or {},
    )
    db.session.add(row)
    return row


def mark_fraud_check_reviewed(check: RegistrationFraudCheck, reviewer: User, *, note: str | None = None) -> None:
    check.reviewed_at = datetime.utcnow()
    check.reviewed_by = reviewer
    check.review_note = (note or "").strip()[:255] or None
