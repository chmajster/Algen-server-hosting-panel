from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional


class ExternalBackupTargetForm(FlaskForm):
    name = StringField("Nazwa targetu", validators=[DataRequired(), Length(min=2, max=120)])
    provider = SelectField(
        "Provider",
        choices=[("s3", "S3-compatible"), ("b2", "Backblaze B2")],
        validators=[DataRequired()],
    )
    endpoint_url = StringField("Endpoint URL", validators=[Optional(), Length(max=500)])
    bucket_name = StringField("Bucket", validators=[DataRequired(), Length(max=255)])
    region = StringField("Region", validators=[Optional(), Length(max=64)])
    access_key_env = StringField("ENV access key", validators=[DataRequired(), Length(max=120)])
    secret_key_env = StringField("ENV secret key", validators=[DataRequired(), Length(max=120)])
    is_active = BooleanField("Aktywny", default=False)
    submit = SubmitField("Zapisz")
