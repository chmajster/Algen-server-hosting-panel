from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import StringField, SubmitField
from wtforms.validators import DataRequired, Length


class ClientTopupForm(FlaskForm):
    amount = StringField("Kwota doladowania", validators=[DataRequired(), Length(max=32)])
    submit = SubmitField("Przejdz do platnosci")
