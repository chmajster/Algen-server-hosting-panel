from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional


class UploadForm(FlaskForm):
    target_dir = StringField("Katalog", validators=[Optional(), Length(max=255)])
    file = FileField("Plik", validators=[FileRequired()])
    submit = SubmitField("Wyślij")


class TextFileForm(FlaskForm):
    path = StringField("Ścieżka", validators=[DataRequired(), Length(max=255)])
    content = TextAreaField("Zawartość", validators=[DataRequired()])
    submit = SubmitField("Zapisz")


class CreateFolderForm(FlaskForm):
    path = StringField("Nowy folder", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Utwórz folder")


class RenamePathForm(FlaskForm):
    source = StringField("Źródło", validators=[DataRequired(), Length(max=255)])
    destination = StringField("Cel", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Zapisz")
