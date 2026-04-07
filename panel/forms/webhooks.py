from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, SelectMultipleField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, URL


class WebhookEndpointForm(FlaskForm):
    name = StringField("Nazwa", validators=[DataRequired(), Length(min=3, max=120)])
    target_url = StringField("Docelowy URL", validators=[DataRequired(), Length(max=500), URL(require_tld=False)])
    secret = StringField("Secret (opcjonalnie)", validators=[Optional(), Length(max=255)])
    client_id = SelectField("Zakres klienta", coerce=int, validators=[DataRequired()], choices=[])
    event_types = SelectMultipleField("Typy eventow", validators=[Optional()], choices=[])
    is_active = BooleanField("Aktywny", default=True)
    submit = SubmitField("Zapisz")
