from __future__ import annotations

import os

from flask import Flask, redirect, url_for

from panel.config import config_map
from panel.extensions import bcrypt, csrf, db, limiter, login_manager, migrate


def create_app(config_name: str | None = None) -> Flask:
    app = Flask(__name__)
    if isinstance(config_name, dict):
        app.config.from_object(config_map["development"])
        app.config.update(config_name)
    else:
        env_name = config_name or os.getenv("APP_ENV", "development")
        app.config.from_object(config_map.get(env_name, config_map["development"]))
    app.config["RATELIMIT_STORAGE_URI"] = app.config.get("RATELIMIT_STORAGE_URI", "memory://")

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)
    bcrypt.init_app(app)
    limiter.init_app(app)

    login_manager.login_view = "auth.login"
    login_manager.login_message = "Zaloguj się, aby kontynuować."
    login_manager.login_message_category = "warning"

    register_blueprints(app)
    register_cli(app)
    register_context(app)
    register_error_handlers(app)

    @app.route("/")
    def index():
        return redirect(url_for("auth.login"))

    return app


def register_blueprints(app: Flask) -> None:
    from panel.admin.routes import admin_bp
    from panel.auth.routes import auth_bp
    from panel.backups.routes import backups_bp
    from panel.billing.routes import billing_bp
    from panel.client.routes import client_bp
    from panel.databases.routes import databases_bp
    from panel.dns.routes import dns_bp
    from panel.domains.routes import domains_bp
    from panel.files.routes import files_bp
    from panel.ftp.routes import ftp_bp
    from panel.hosts.routes import hosts_bp
    from panel.mail.routes import mail_bp
    from panel.monitoring.routes import monitoring_bp
    from panel.ssl.routes import ssl_bp

    for blueprint in [
        auth_bp,
        admin_bp,
        client_bp,
        databases_bp,
        billing_bp,
        domains_bp,
        dns_bp,
        ftp_bp,
        mail_bp,
        ssl_bp,
        backups_bp,
        files_bp,
        monitoring_bp,
        hosts_bp,
    ]:
        app.register_blueprint(blueprint)


def register_cli(app: Flask) -> None:
    import click

    from panel.extensions import db
    from panel.seed import seed_defaults
    from panel.services.billing import run_billing_cycle

    @app.cli.command("seed-data")
    @click.option("--admin-username", default="admin")
    @click.option("--admin-password", default="ChangeMe123!")
    def seed_data(admin_username: str, admin_password: str):
        seed_defaults(admin_username=admin_username, admin_password=admin_password)
        click.echo("Dane startowe utworzone.")

    @app.cli.command("create-admin")
    @click.option("--username", required=True)
    @click.option("--password", required=True)
    @click.option("--email", required=True)
    def create_admin(username: str, password: str, email: str):
        from panel.models import Role, User

        role = Role.query.filter_by(name="administrator").first()
        if role is None:
            role = Role(name="administrator", description="Administrator")
            db.session.add(role)
            db.session.flush()
        user = User(
            role=role,
            username=username,
            email=email,
            first_name="Admin",
            last_name="User",
            status="active",
        )
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Administrator {username} został utworzony.")

    @app.cli.command("run-billing")
    def run_billing():
        processed = run_billing_cycle()
        db.session.commit()
        click.echo(f"Przetworzono cykli: {processed}")


def register_context(app: Flask) -> None:
    from panel.models import ActivityLog

    @app.context_processor
    def inject_globals():
        return {
            "recent_logs": ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(5).all(),
        }


def register_error_handlers(app: Flask) -> None:
    from flask import render_template

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors.html", title="Brak dostępu", error_code=403), 403

    @app.errorhandler(404)
    def missing(error):
        return render_template("errors.html", title="Nie znaleziono", error_code=404), 404

    @app.errorhandler(500)
    def internal(error):
        db.session.rollback()
        return render_template("errors.html", title="Błąd serwera", error_code=500), 500
