from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from panel import create_app
from panel.extensions import db
from panel.models import Client, ClientBalance, Role, User


@pytest.fixture()
def app():
    temp_dir = tempfile.TemporaryDirectory()
    app = create_app(
        {
            "TESTING": True,
            "WTF_CSRF_ENABLED": False,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "STORAGE_ROOT": str(Path(temp_dir.name) / "uploads"),
            "CLIENT_HOME_ROOT": str(Path(temp_dir.name) / "clients"),
            "BACKUP_ROOT": str(Path(temp_dir.name) / "backups"),
            "SMOKE_TEST_LOG_FILE": str(Path(temp_dir.name) / "smoke-test.log"),
            "SMOKE_TEST_API_TOKEN": "test-smoke-token",
            "SMOKE_TEST_API_ALLOWLIST": "127.0.0.1/32,::1/128",
            "SMOKE_TEST_API_RATELIMIT": "200 per minute",
            "TWO_FACTOR_AVAILABLE": True,
            "TWO_FACTOR_ISSUER": "Hosting Panel Test",
            "TWO_FACTOR_EMAIL_ENABLED": True,
            "TWO_FACTOR_EMAIL_CODE_TTL_SECONDS": 300,
            "TWO_FACTOR_EMAIL_SUBJECT": "Kod testowy 2FA",
            "ONLINE_PAYMENTS_ENABLED": False,
            "ONLINE_PAYMENTS_PROVIDER": "mock",
            "ONLINE_PAYMENTS_CURRENCY": "PLN",
            "ONLINE_PAYMENTS_MIN_AMOUNT": "5.00",
            "ONLINE_PAYMENTS_MAX_AMOUNT": "50000.00",
            "ADMIN_LOCAL_ONLY": True,
            "ADMIN_ALLOWED_NETWORKS": "127.0.0.1/32,::1/128",
        }
    )
    with app.app_context():
        db.create_all()
        admin_role = Role(name="administrator", description="Admin")
        client_role = Role(name="client", description="Client")
        db.session.add_all([admin_role, client_role])
        admin = User(
            role=admin_role,
            username="admin",
            email="admin@test.local",
            first_name="Admin",
            last_name="User",
            status="active",
        )
        admin.set_password("Admin123!")
        client_user = User(
            role=client_role,
            username="client",
            email="client@test.local",
            first_name="Client",
            last_name="User",
            status="active",
        )
        client_user.set_password("Client123!")
        client = Client(user=client_user, company_name="Test Client", resource_limits={"domains": 3})
        client.balance = ClientBalance(balance=50, currency="PLN")
        db.session.add_all([admin, client_user, client])
        db.session.commit()
        yield app
        db.session.remove()
        db.drop_all()
    temp_dir.cleanup()


@pytest.fixture()
def client(app):
    return app.test_client()
