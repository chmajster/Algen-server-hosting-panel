from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    username = StringField("Login lub e-mail", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Haslo", validators=[DataRequired(), Length(max=255)])
    remember_me = BooleanField("Zapamietaj mnie")
    submit = SubmitField("Zaloguj")
