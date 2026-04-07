import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Length, Optional, ValidationError


EMAIL_SIMPLE_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9.-]{1,255}$")


class LoginForm(FlaskForm):
    username = StringField("Login lub e-mail", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Haslo", validators=[DataRequired(), Length(max=255)])
    remember_me = BooleanField("Zapamietaj mnie")
    submit = SubmitField("Zaloguj")


class RegisterForm(FlaskForm):
    first_name = StringField("Imie", validators=[DataRequired(), Length(max=120)])
    last_name = StringField("Nazwisko", validators=[DataRequired(), Length(max=120)])
    username = StringField("Login", validators=[DataRequired(), Length(min=3, max=80)])
    email = StringField("E-mail", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Haslo", validators=[DataRequired(), Length(min=8, max=255)])
    password_confirm = PasswordField(
        "Powtorz haslo",
        validators=[DataRequired(), EqualTo("password", message="Hasla musza byc identyczne.")],
    )
    plan_id = SelectField("Plan", coerce=int, validators=[DataRequired()], choices=[])
    submit = SubmitField("Utworz konto")

    def validate_email(self, field):
        value = (field.data or "").strip().lower()
        if EMAIL_SIMPLE_RE.fullmatch(value) is None:
            raise ValidationError("Nieprawidlowy adres e-mail.")
        field.data = value


class TwoFactorChallengeForm(FlaskForm):
    code = StringField("Kod 2FA", validators=[DataRequired(), Length(min=6, max=12)])
    submit = SubmitField("Zweryfikuj")


class TwoFactorEnableTotpForm(FlaskForm):
    code = StringField("Kod z aplikacji", validators=[DataRequired(), Length(min=6, max=12)])
    submit = SubmitField("Wlacz Google Authenticator")


class TwoFactorEnableEmailForm(FlaskForm):
    password = PasswordField("Aktualne haslo", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Wlacz 2FA przez e-mail")


class TwoFactorDisableForm(FlaskForm):
    password = PasswordField("Aktualne haslo", validators=[DataRequired(), Length(max=255)])
    code = StringField("Kod 2FA", validators=[Optional(), Length(min=6, max=12)])
    submit = SubmitField("Wylacz 2FA")
