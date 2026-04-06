from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, IntegerField, PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from panel.forms.password_policy import strong_password_validators


class ServicePlanForm(FlaskForm):
    name = StringField("Nazwa planu", validators=[DataRequired(), Length(max=120)])
    code = StringField("Kod", validators=[DataRequired(), Length(max=80)])
    monthly_price = StringField("Cena miesieczna", validators=[DataRequired(), Length(max=32)])
    daily_price = StringField("Cena dzienna", validators=[Optional(), Length(max=32)])
    yearly_price = StringField("Cena roczna", validators=[Optional(), Length(max=32)])
    description = TextAreaField("Opis", validators=[Optional(), Length(max=2000)])
    submit = SubmitField("Zapisz")


class ClientServiceForm(FlaskForm):
    client_id = SelectField("Klient", coerce=int, validators=[DataRequired()])
    service_plan_id = SelectField("Plan", coerce=int, validators=[Optional()])
    name = StringField("Nazwa uslugi", validators=[DataRequired(), Length(max=120)])
    service_type = SelectField(
        "Typ uslugi",
        choices=[
            ("hosting", "Hosting"),
            ("domain", "Domena"),
            ("database", "Baza danych"),
            ("ftp", "FTP"),
            ("mail", "Poczta"),
            ("ssl", "SSL"),
            ("backup", "Backup"),
        ],
        validators=[DataRequired()],
    )
    billing_period = SelectField(
        "Okres rozliczenia",
        choices=[("daily", "Dzienny"), ("monthly", "Miesieczny"), ("yearly", "Roczny")],
        validators=[DataRequired()],
    )
    recurring_amount = StringField("Kwota cykliczna", validators=[DataRequired(), Length(max=32)])
    status = SelectField(
        "Status",
        choices=[
            ("active", "Aktywne"),
            ("pending_payment", "Oczekuje na platnosc"),
            ("suspended", "Zawieszone"),
            ("blocked_manual", "Zablokowane recznie"),
            ("deleted", "Usuniete"),
        ],
        validators=[DataRequired()],
    )
    starts_on = DateField("Start", validators=[DataRequired()], format="%Y-%m-%d")
    auto_suspend = BooleanField("Auto-zawieszenie", default=True)
    auto_resume = BooleanField("Auto-wznowienie", default=True)
    submit = SubmitField("Zapisz")


class DomainForm(FlaskForm):
    client_id = SelectField("Klient", coerce=int, validators=[DataRequired()])
    client_service_id = SelectField("Usluga", coerce=int, validators=[Optional()])
    name = StringField("Domena", validators=[DataRequired(), Length(max=255)])
    document_root = StringField("Katalog docelowy", validators=[Optional(), Length(max=255)])
    php_version = StringField("Wersja PHP", validators=[DataRequired(), Length(max=16)])
    status = SelectField(
        "Status",
        choices=[("active", "Aktywna"), ("disabled", "Wylaczona"), ("pending_payment", "Oczekuje na platnosc")],
        validators=[DataRequired()],
    )
    is_primary = BooleanField("Domena glowna")
    submit = SubmitField("Zapisz")


class SubdomainForm(FlaskForm):
    name = StringField("Subdomena", validators=[DataRequired(), Length(max=255)])
    document_root = StringField("Katalog docelowy", validators=[Optional(), Length(max=255)])
    php_version = StringField("Wersja PHP", validators=[DataRequired(), Length(max=16)])
    status = SelectField("Status", choices=[("active", "Aktywna"), ("disabled", "Wylaczona")], validators=[DataRequired()])
    submit = SubmitField("Zapisz")


class DatabaseForm(FlaskForm):
    client_id = SelectField("Klient", coerce=int, validators=[DataRequired()])
    client_service_id = SelectField("Usluga", coerce=int, validators=[Optional()])
    name = StringField("Nazwa bazy", validators=[DataRequired(), Length(max=120)])
    engine = SelectField("Silnik", choices=[("mariadb", "MariaDB"), ("mysql", "MySQL")], validators=[DataRequired()])
    charset = StringField("Charset", validators=[DataRequired(), Length(max=32)])
    collation = StringField("Collation", validators=[DataRequired(), Length(max=64)])
    status = SelectField("Status", choices=[("active", "Aktywna"), ("disabled", "Wylaczona")], validators=[DataRequired()])
    submit = SubmitField("Zapisz")


class DatabaseUserForm(FlaskForm):
    database_id = SelectField("Baza", coerce=int, validators=[DataRequired()])
    username = StringField("Uzytkownik DB", validators=[DataRequired(), Length(max=120)])
    password = PasswordField("Haslo", validators=strong_password_validators(required=False))
    host = StringField("Host", validators=[DataRequired(), Length(max=120)])
    status = SelectField("Status", choices=[("active", "Aktywny"), ("disabled", "Wylaczony")], validators=[DataRequired()])
    submit = SubmitField("Zapisz")


class FTPAccountForm(FlaskForm):
    client_id = SelectField("Klient", coerce=int, validators=[DataRequired()])
    client_service_id = SelectField("Usluga", coerce=int, validators=[Optional()])
    username = StringField("Login FTP", validators=[DataRequired(), Length(max=120)])
    password = PasswordField("Haslo", validators=strong_password_validators(required=False))
    home_directory = StringField("Katalog domowy", validators=[DataRequired(), Length(max=255)])
    is_active = BooleanField("Aktywne", default=True)
    submit = SubmitField("Zapisz")


class DNSZoneForm(FlaskForm):
    client_id = SelectField("Klient", coerce=int, validators=[DataRequired()])
    domain_id = SelectField("Domena", coerce=int, validators=[DataRequired()])
    name = StringField("Nazwa strefy", validators=[DataRequired(), Length(max=255)])
    default_ttl = IntegerField("TTL", validators=[DataRequired(), NumberRange(min=60, max=86400)])
    is_active = BooleanField("Aktywna", default=True)
    submit = SubmitField("Zapisz")


class DNSRecordForm(FlaskForm):
    zone_id = SelectField("Strefa", coerce=int, validators=[DataRequired()])
    name = StringField("Nazwa", validators=[DataRequired(), Length(max=255)])
    type = SelectField("Typ", choices=[("A", "A"), ("AAAA", "AAAA"), ("CNAME", "CNAME"), ("MX", "MX"), ("TXT", "TXT"), ("NS", "NS")], validators=[DataRequired()])
    value = StringField("Wartosc", validators=[DataRequired(), Length(max=255)])
    priority = IntegerField("Priorytet", validators=[Optional(), NumberRange(min=0, max=65535)])
    ttl = IntegerField("TTL", validators=[DataRequired(), NumberRange(min=60, max=86400)])
    disabled = BooleanField("Wylaczony")
    submit = SubmitField("Zapisz")


class SSLCertificateForm(FlaskForm):
    target_ref = SelectField("Witryna", validators=[DataRequired()])
    provider = SelectField("Provider", choices=[("letsencrypt", "Let's Encrypt"), ("manual", "Manual")], validators=[DataRequired()])
    status = SelectField("Status", choices=[("pending", "Oczekuje"), ("active", "Aktywny"), ("expired", "Wygasl")], validators=[DataRequired()])
    auto_renew = BooleanField("Automatyczne odnawianie", default=True)
    certificate_path = StringField("Sciezka certyfikatu", validators=[Optional(), Length(max=255)])
    private_key_path = StringField("Sciezka klucza", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Zapisz")


class MailboxForm(FlaskForm):
    client_id = SelectField("Klient", coerce=int, validators=[DataRequired()])
    domain_id = SelectField("Domena", coerce=int, validators=[DataRequired()])
    email = StringField("Adres e-mail", validators=[DataRequired(), Length(max=255)])
    password = PasswordField("Haslo", validators=strong_password_validators(required=False))
    quota_mb = IntegerField("Quota MB", validators=[DataRequired(), NumberRange(min=10, max=102400)])
    status = SelectField("Status", choices=[("active", "Aktywna"), ("disabled", "Wylaczona")], validators=[DataRequired()])
    submit = SubmitField("Zapisz")


class MailAliasForm(FlaskForm):
    mailbox_id = SelectField("Skrzynka", coerce=int, validators=[DataRequired()])
    source = StringField("Alias", validators=[DataRequired(), Length(max=255)])
    destination = StringField("Cel", validators=[DataRequired(), Length(max=255)])
    alias_type = SelectField("Typ", choices=[("alias", "Alias"), ("forwarder", "Forwarder")], validators=[DataRequired()])
    submit = SubmitField("Zapisz")


class BackupForm(FlaskForm):
    client_id = SelectField("Klient", coerce=int, validators=[DataRequired()])
    domain_id = SelectField("Domena", coerce=int, validators=[Optional()])
    database_id = SelectField("Baza", coerce=int, validators=[Optional()])
    backup_type = SelectField("Typ", choices=[("client", "Klient"), ("domain", "Domena"), ("database", "Baza danych")], validators=[DataRequired()])
    storage_path = StringField("Sciezka archiwum", validators=[DataRequired(), Length(max=255)])
    scheduled_for = DateField("Zaplanowano na", validators=[Optional()], format="%Y-%m-%d")
    submit = SubmitField("Zapisz")
