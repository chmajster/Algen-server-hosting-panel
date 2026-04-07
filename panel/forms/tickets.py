from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from panel.services.ticket_macros import macro_category_choices, macro_visibility_choices


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
    macro_id = SelectField("Makro (opcjonalnie)", coerce=int, validators=[Optional()], choices=[(0, "Brak")])
    message = TextAreaField("Wiadomosc", validators=[DataRequired(), Length(min=2, max=8000)])
    submit = SubmitField("Wyslij odpowiedz")


class TicketStaffReplyForm(FlaskForm):
    macro_id = SelectField("Makro (opcjonalnie)", coerce=int, validators=[Optional()], choices=[(0, "Brak")])
    message = TextAreaField("Wiadomosc", validators=[Optional(), Length(max=8000)])
    submit = SubmitField("Wyslij odpowiedz")


class TicketAdminUpdateForm(FlaskForm):
    status = SelectField("Status", validators=[DataRequired()], choices=TICKET_STATUS_CHOICES)
    priority = SelectField("Priorytet", validators=[DataRequired()], choices=TICKET_PRIORITY_CHOICES)
    assigned_to_user_id = SelectField("Przypisany operator", coerce=int, validators=[Optional()], choices=[(0, "Nieprzypisany")])
    submit = SubmitField("Zapisz ustawienia")


class TicketMacroForm(FlaskForm):
    name = StringField("Nazwa", validators=[DataRequired(), Length(min=3, max=120)])
    category = SelectField("Kategoria", validators=[DataRequired()], choices=macro_category_choices())
    visibility_scope = SelectField("Widocznosc", validators=[DataRequired()], choices=macro_visibility_choices())
    subject_template = StringField("Temat (opcjonalnie)", validators=[Optional(), Length(max=200)])
    body_template = TextAreaField("Tresc makra", validators=[DataRequired(), Length(min=2, max=8000)])
    sort_order = IntegerField("Kolejnosc", validators=[Optional()])
    is_active = BooleanField("Aktywne", default=True)
    submit = SubmitField("Zapisz makro")
