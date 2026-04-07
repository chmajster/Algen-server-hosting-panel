from __future__ import annotations

from wtforms import BooleanField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.fields.datetime import DateTimeLocalField
from wtforms.validators import DataRequired, Length, Optional

from flask_wtf import FlaskForm


STATUS_STATE_CHOICES = [
    ("operational", "operational"),
    ("degraded_performance", "degraded performance"),
    ("partial_outage", "partial outage"),
    ("major_outage", "major outage"),
    ("maintenance", "maintenance"),
    ("resolved", "resolved"),
]


class StatusEventForm(FlaskForm):
    event_type = SelectField(
        "Typ zdarzenia",
        choices=[("incident", "Incydent"), ("maintenance", "Maintenance")],
        validators=[DataRequired()],
    )
    state = SelectField("Stan", choices=STATUS_STATE_CHOICES, validators=[DataRequired()])
    title = StringField("Tytul", validators=[DataRequired(), Length(min=3, max=200)])
    public_message = TextAreaField("Komunikat publiczny", validators=[DataRequired(), Length(min=3, max=5000)])
    internal_note = TextAreaField("Notatka wewnetrzna", validators=[Optional(), Length(max=5000)])
    affected_components = StringField(
        "Komponenty (po przecinku)",
        validators=[Optional(), Length(max=500)],
    )
    starts_at = DateTimeLocalField("Start", validators=[DataRequired()], format="%Y-%m-%dT%H:%M")
    ends_at = DateTimeLocalField("Koniec", validators=[Optional()], format="%Y-%m-%dT%H:%M")
    is_public = BooleanField("Widoczne dla klienta", default=True)
    submit = SubmitField("Zapisz")
