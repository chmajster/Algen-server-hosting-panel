from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


TICKET_PRIORITY_CHOICES = [
    ("low", "Niski"),
    ("normal", "Normalny"),
    ("high", "Wysoki"),
    ("urgent", "Pilny"),
]

TICKET_STATUS_CHOICES = [
    ("open", "Otwarte"),
    ("answered", "Odpowiedziane"),
    ("pending", "Oczekuje"),
    ("closed", "Zamkniete"),
]

TICKET_CATEGORY_CHOICES = [
    ("hosting", "Hosting"),
    ("billing", "Billing"),
    ("domain", "Domeny"),
    ("mail", "Poczta"),
    ("other", "Inne"),
]


class TicketCreateForm(FlaskForm):
    subject = StringField("Temat", validators=[DataRequired(), Length(min=5, max=200)])
    category = SelectField("Kategoria", validators=[DataRequired()], choices=TICKET_CATEGORY_CHOICES)
    priority = SelectField("Priorytet", validators=[DataRequired()], choices=TICKET_PRIORITY_CHOICES, default="normal")
    message = TextAreaField("Wiadomosc", validators=[DataRequired(), Length(min=5, max=8000)])
    submit = SubmitField("Utworz ticket")


class TicketReplyForm(FlaskForm):
    message = TextAreaField("Wiadomosc", validators=[DataRequired(), Length(min=2, max=8000)])
    submit = SubmitField("Wyslij odpowiedz")


class TicketAdminUpdateForm(FlaskForm):
    status = SelectField("Status", validators=[DataRequired()], choices=TICKET_STATUS_CHOICES)
    priority = SelectField("Priorytet", validators=[DataRequired()], choices=TICKET_PRIORITY_CHOICES)
    assigned_to_user_id = SelectField("Przypisany operator", coerce=int, validators=[Optional()], choices=[(0, "Nieprzypisany")])
    submit = SubmitField("Zapisz ustawienia")
