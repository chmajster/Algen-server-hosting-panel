from __future__ import annotations

from decimal import Decimal

from panel.extensions import db
from panel.models import (
    Client,
    ClientBalance,
    PaymentSetting,
    Role,
    ServicePlan,
    SystemSetting,
    User,
)


def seed_defaults(
    admin_username: str = "admin",
    admin_password: str = "ChangeMe123!",
    admin_email: str = "admin@example.com",
) -> User:
    roles = {
        "administrator": Role.query.filter_by(name="administrator").first(),
        "client": Role.query.filter_by(name="client").first(),
    }
    for name in roles:
        if roles[name] is None:
            roles[name] = Role(name=name, description=f"Rola {name}")
            db.session.add(roles[name])
    db.session.flush()

    admin = User.query.filter_by(username=admin_username).first()
    if admin is None:
        admin = User(
            role=roles["administrator"],
            username=admin_username,
            email=admin_email,
            first_name="System",
            last_name="Administrator",
            status="active",
        )
        admin.set_password(admin_password)
        db.session.add(admin)
    else:
        admin.role = roles["administrator"]
        admin.email = admin_email
        admin.status = "active"

    if not ServicePlan.query.filter_by(code="starter").first():
        db.session.add(
            ServicePlan(
                name="Starter Hosting",
                code="starter",
                description="Domyslny plan startowy",
                monthly_price=Decimal("49.00"),
                daily_price=Decimal("2.00"),
                yearly_price=Decimal("490.00"),
                limits_json={
                    "domains": 5,
                    "databases": 5,
                    "ftp_accounts": 5,
                    "mailboxes": 20,
                    "disk_mb": 10240,
                },
            )
        )

    default_settings = {
        "billing.auto_resume": "Automatyczne wznawianie uslug po doladowaniu salda",
        "ui.css_framework": "Aktywny framework CSS panelu",
    }
    for key, description in default_settings.items():
        if not SystemSetting.query.filter_by(key=key).first():
            db.session.add(
                SystemSetting(
                    key=key,
                    value="true" if key == "billing.auto_resume" else "bootstrap",
                    description=description,
                )
            )

    client_user = User.query.filter_by(username="client1").first()
    if client_user is None:
        client_user = User(
            role=roles["client"],
            username="client1",
            email="client1@example.com",
            first_name="Jan",
            last_name="Klient",
            status="active",
        )
        client_user.set_password("Client123!")
        client = Client(
            user=client_user,
            company_name="Demo Client Sp. z o.o.",
            phone="+48 500 600 700",
            resource_limits={
                "domains": 3,
                "databases": 2,
                "ftp_accounts": 2,
                "mailboxes": 10,
            },
        )
        client.balance = ClientBalance(balance=Decimal("120.00"), currency="PLN")
        db.session.add(client_user)
        db.session.add(client)
        db.session.add(PaymentSetting(client=client, grace_days=3, auto_resume=True))

    db.session.commit()
    return admin
