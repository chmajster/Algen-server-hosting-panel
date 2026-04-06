from __future__ import annotations

from decimal import Decimal

from panel.extensions import db
from panel.models import BillingTransaction, Client, SystemSetting, User
from panel.seed import seed_defaults
from panel.services.billing import adjust_balance


def test_login_success(client):
    response = client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Ostatnie logi operacji" in response.get_data(as_text=True)


def test_client_cannot_open_admin_panel(client):
    client.post("/auth/login", data={"username": "client", "password": "Client123!"}, follow_redirects=True)
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 403


def test_balance_adjustment_creates_transaction(app):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        client_profile = Client.query.first()
        adjust_balance(client_profile, Decimal("25.00"), "topup", "Test top-up", actor=user)
        db.session.commit()
        tx = BillingTransaction.query.order_by(BillingTransaction.id.desc()).first()
        assert tx.amount == Decimal("25.00")
        assert client_profile.balance.balance == Decimal("75.00")


def test_admin_can_change_css_framework(client, app):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post(
        "/admin/settings",
        data={"css_framework": "bulma"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        setting = SystemSetting.query.filter_by(key="ui.css_framework").first()
        assert setting is not None
        assert setting.value == "bulma"


def test_seed_preserves_existing_admin_password(app):
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        assert admin is not None
        assert admin.check_password("Admin123!")
        seed_defaults(
            admin_username="admin",
            admin_password="NewPassword123!",
            admin_email="admin@test.local",
        )
        db.session.refresh(admin)
        assert admin.check_password("Admin123!")
        assert not admin.check_password("NewPassword123!")
