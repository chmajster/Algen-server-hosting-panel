from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional

from panel.forms.password_policy import strong_password_validators


class UserForm(FlaskForm):
    username = StringField("Login", validators=[DataRequired(), Length(min=3, max=80)])
    email = EmailField("E-mail", validators=[DataRequired(), Email(), Length(max=255)])
    first_name = StringField("Imie", validators=[DataRequired(), Length(max=120)])
    last_name = StringField("Nazwisko", validators=[DataRequired(), Length(max=120)])
    password = PasswordField("Haslo", validators=strong_password_validators(required=False))
    role = SelectField(
        "Rola",
        choices=[("administrator", "Administrator"), ("client", "Klient")],
        validators=[DataRequired()],
    )
    status = SelectField(
        "Status",
        choices=[
            ("active", "Aktywny"),
            ("suspended_financial", "Zawieszony finansowo"),
            ("blocked_manual", "Zablokowany recznie"),
            ("inactive", "Nieaktywny"),
        ],
        validators=[DataRequired()],
    )
    company_name = StringField("Firma", validators=[Optional(), Length(max=255)])
    phone = StringField("Telefon", validators=[Optional(), Length(max=50)])
    notes = TextAreaField("Notatki", validators=[Optional(), Length(max=2000)])
    allow_dns_management = BooleanField("Pozwol klientowi zarzadzac DNS", default=True)
    auto_resume_services = BooleanField("Automatycznie wznawiaj uslugi", default=True)
    submit = SubmitField("Zapisz")


class PasswordResetForm(FlaskForm):
    password = PasswordField("Nowe haslo", validators=strong_password_validators(required=True))
    submit = SubmitField("Zresetuj haslo")


class BalanceAdjustmentForm(FlaskForm):
    amount = StringField("Kwota", validators=[DataRequired(), Length(max=32)])
    transaction_type = SelectField(
        "Typ operacji",
        choices=[
            ("topup", "Doladowanie"),
            ("deduction", "Odjecie srodkow"),
            ("bonus", "Bonus"),
            ("correction", "Korekta"),
            ("refund", "Zwrot"),
            ("manual_fee", "Reczna oplata"),
        ],
        validators=[DataRequired()],
    )
    description = StringField("Opis", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Zaksieguj")


class AppearanceSettingsForm(FlaskForm):
    css_framework = SelectField("Framework CSS", validators=[DataRequired()], choices=[])
    submit = SubmitField("Zapisz ustawienia")
