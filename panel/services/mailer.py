from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app


def send_plain_email(*, to_email: str, subject: str, body: str) -> str | None:
    server = (current_app.config.get("MAIL_SERVER") or "").strip()
    if not server:
        if current_app.config.get("TESTING"):
            return None
        return "Brak konfiguracji MAIL_SERVER dla wysylki e-mail."

    use_ssl = bool(current_app.config.get("MAIL_USE_SSL", False))
    use_tls = bool(current_app.config.get("MAIL_USE_TLS", True)) and not use_ssl
    default_port = 465 if use_ssl else (587 if use_tls else 25)
    port = int(current_app.config.get("MAIL_PORT", default_port))
    username = (current_app.config.get("MAIL_USERNAME") or "").strip() or None
    password = (current_app.config.get("MAIL_PASSWORD") or "").strip() or None
    from_email = (current_app.config.get("MAIL_FROM") or "").strip() or "no-reply@localhost"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = from_email
    message["To"] = to_email
    message.set_content(body)

    ssl_context = ssl.create_default_context()
    try:
        if use_ssl:
            with smtplib.SMTP_SSL(server, port, timeout=20, context=ssl_context) as smtp:
                if username and password:
                    smtp.login(username, password)
                smtp.send_message(message)
            return None

        with smtplib.SMTP(server, port, timeout=20) as smtp:
            smtp.ehlo()
            if use_tls:
                smtp.starttls(context=ssl_context)
                smtp.ehlo()
            if username and password:
                smtp.login(username, password)
            smtp.send_message(message)
        return None
    except Exception as exc:
        return str(exc)
