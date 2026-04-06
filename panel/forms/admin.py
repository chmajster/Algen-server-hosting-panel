from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, Optional


class UserForm(FlaskForm):
    username = StringField("Login", validators=[DataRequired(), Length(min=3, max=80)])
    email = EmailField("E-mail", validators=[DataRequired(), Email(), Length(max=255)])
    first_name = StringField("Imię", validators=[DataRequired(), Length(max=120)])
    last_name = StringField("Nazwisko", validators=[DataRequired(), Length(max=120)])
    password = PasswordField("Hasło", validators=[Optional(), Length(min=8, max=255)])
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
            ("blocked_manual", "Zablokowany ręcznie"),
            ("inactive", "Nieaktywny"),
        ],
        validators=[DataRequired()],
    )
    company_name = StringField("Firma", validators=[Optional(), Length(max=255)])
    phone = StringField("Telefon", validators=[Optional(), Length(max=50)])
    notes = TextAreaField("Notatki", validators=[Optional(), Length(max=2000)])
    allow_dns_management = BooleanField("Pozwól klientowi zarządzać DNS", default=True)
    auto_resume_services = BooleanField("Automatycznie wznawiaj usługi", default=True)
    submit = SubmitField("Zapisz")


class PasswordResetForm(FlaskForm):
    password = PasswordField("Nowe hasło", validators=[DataRequired(), Length(min=8, max=255)])
    submit = SubmitField("Zresetuj hasło")


class BalanceAdjustmentForm(FlaskForm):
    amount = StringField("Kwota", validators=[DataRequired(), Length(max=32)])
    transaction_type = SelectField(
        "Typ operacji",
        choices=[
            ("topup", "Doładowanie"),
            ("deduction", "Odjęcie środków"),
            ("bonus", "Bonus"),
            ("correction", "Korekta"),
            ("refund", "Zwrot"),
            ("manual_fee", "Ręczna opłata"),
        ],
        validators=[DataRequired()],
    )
    description = StringField("Opis", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Zaksięguj")
