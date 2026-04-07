from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class AutomationRuleForm(FlaskForm):
    name = StringField("Nazwa reguly", validators=[DataRequired(), Length(min=3, max=120)])
    description = StringField("Opis", validators=[Optional(), Length(max=255)])
    trigger_event = StringField("Zdarzenie wyzwalajace", validators=[DataRequired(), Length(min=3, max=120)])
    conditions_json = TextAreaField("Warunki JSON", validators=[Optional(), Length(max=4000)])
    actions_json = TextAreaField("Akcje JSON", validators=[DataRequired(), Length(min=2, max=8000)])
    stop_on_match = BooleanField("Zatrzymaj po dopasowaniu", default=False)
    is_active = BooleanField("Regula aktywna", default=True)
    submit = SubmitField("Zapisz regule")


class AutomationManualTriggerForm(FlaskForm):
    trigger_event = StringField("Zdarzenie", validators=[DataRequired(), Length(min=3, max=120)])
    payload_json = TextAreaField("Payload JSON", validators=[Optional(), Length(max=8000)])
    submit = SubmitField("Wyzwol reguly")
