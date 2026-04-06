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
            "BACKUP_ROOT": str(Path(temp_dir.name) / "backups"),
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
