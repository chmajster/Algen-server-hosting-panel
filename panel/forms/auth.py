from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    username = StringField("Login lub e-mail", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Hasło", validators=[DataRequired(), Length(min=8, max=255)])
    submit = SubmitField("Zaloguj")
