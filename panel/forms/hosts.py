import ipaddress
import re

from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, ValidationError


HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(?:\.(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?))*$"
)


class HostsEntryForm(FlaskForm):
    ip_address = StringField("Adres IP", validators=[DataRequired(), Length(max=45)])
    hostname = StringField("Hostname", validators=[DataRequired(), Length(max=255)])
    previous_value = StringField("Poprzedni IP", validators=[Length(max=45)])
    confirm_critical = BooleanField("Potwierdzam zmianę krytycznego wpisu")
    submit = SubmitField("Wykonaj")

    def validate_ip_address(self, field):
        value = (field.data or "").strip()
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ValidationError("Nieprawidlowy adres IP.")
        field.data = value

    def validate_previous_value(self, field):
        value = (field.data or "").strip()
        if not value:
            field.data = ""
            return
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ValidationError("Nieprawidlowa poprzednia wartosc IP.")
        field.data = value

    def validate_hostname(self, field):
        value = (field.data or "").strip().lower()
        if HOSTNAME_RE.fullmatch(value) is None:
            raise ValidationError("Nieprawidlowa nazwa hosta.")
        field.data = value


class RestoreHostsBackupForm(FlaskForm):
    backup_name = StringField("Backup", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Przywróć")
