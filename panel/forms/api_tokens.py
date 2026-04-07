from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length


class ApiTokenCreateForm(FlaskForm):
    name = StringField("Nazwa tokenu", validators=[DataRequired(), Length(min=3, max=120)])
    submit = SubmitField("Wygeneruj token")
