from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class ClientTopupForm(FlaskForm):
    amount = StringField("Kwota doladowania", validators=[DataRequired(), Length(max=32)])
    submit = SubmitField("Przejdz do platnosci")


class ClientPlanChangeForm(FlaskForm):
    target_plan_id = SelectField("Nowy plan", coerce=int, validators=[DataRequired()], choices=[])
    submit = SubmitField("Zmien plan")
