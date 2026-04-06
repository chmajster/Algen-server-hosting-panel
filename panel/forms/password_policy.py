import re

from wtforms import ValidationError
from wtforms.validators import DataRequired, Length, Optional


def strong_password(form, field) -> None:
    password = field.data or ""
    if not password:
        return
    checks = [
        (r"[a-z]", "Haslo musi zawierac co najmniej jedna mala litere."),
        (r"[A-Z]", "Haslo musi zawierac co najmniej jedna duza litere."),
        (r"\d", "Haslo musi zawierac co najmniej jedna cyfre."),
        (r"[^A-Za-z0-9]", "Haslo musi zawierac co najmniej jeden znak specjalny."),
    ]
    for pattern, message in checks:
        if re.search(pattern, password) is None:
            raise ValidationError(message)


def strong_password_validators(*, required: bool) -> list:
    validators = [DataRequired()] if required else [Optional()]
    validators.append(Length(min=8, max=255))
    validators.append(strong_password)
    return validators
