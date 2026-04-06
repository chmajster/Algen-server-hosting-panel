from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class HostsEntryForm(FlaskForm):
    ip_address = StringField("Adres IP", validators=[DataRequired(), Length(max=45)])
    hostname = StringField("Hostname", validators=[DataRequired(), Length(max=255)])
    previous_value = StringField("Poprzedni IP", validators=[Length(max=45)])
    confirm_critical = BooleanField("Potwierdzam zmianę krytycznego wpisu")
    submit = SubmitField("Wykonaj")


class RestoreHostsBackupForm(FlaskForm):
    backup_name = StringField("Backup", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Przywróć")
