from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class MigrationWizardForm(FlaskForm):
    source_provider = SelectField(
        "Zrodlo migracji",
        choices=[
            ("cpanel", "cPanel"),
            ("plesk", "Plesk"),
            ("directadmin", "DirectAdmin"),
            ("other", "Inny panel"),
        ],
        validators=[DataRequired()],
    )
    source_hostname = StringField("Adres serwera zrodlowego", validators=[DataRequired(), Length(min=3, max=255)])
    source_username = StringField("Login zrodlowy", validators=[DataRequired(), Length(min=2, max=120)])
    source_password = PasswordField("Haslo zrodlowe", validators=[Optional(), Length(max=255)])
    source_path = StringField("Sciezka danych (opcjonalnie)", validators=[Optional(), Length(max=255)])
    notes = TextAreaField("Notatki migracji", validators=[Optional(), Length(max=1000)])
    submit = SubmitField("Utworz zgloszenie migracji")
