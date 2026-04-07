from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import io
from pathlib import Path

from panel.extensions import db
from panel.models import (
    AccountSuspension,
    BillingCycle,
    BillingTransaction,
    Client,
    ClientService,
    ApiToken,
    Backup,
    BackupRestoreJob,
    DatabaseUser,
    Domain,
    HostingDatabase,
    Mailbox,
    OnlinePayment,
    SSLCertificate,
    ServicePlan,
    Subdomain,
    SystemSetting,
    Ticket,
    TicketAttachment,
    TicketMessage,
    User,
    WebhookDelivery,
    WebhookEndpoint,
)
from panel.seed import seed_defaults
from panel.services.client_apache import client_apache_resource_limits
from panel.services.api_tokens import issue_api_token
from panel.services.billing import adjust_balance, update_client_financial_status_for_date
from panel.services.two_factor import current_totp, generate_two_factor_secret


def test_login_success(client):
    response = client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Ostatnie logi operacji" in response.get_data(as_text=True)


def test_public_registration_creates_client_with_selected_plan(client, app):
    with app.app_context():
        plan = ServicePlan(
            name="Pro Register",
            code="pro-register",
            description="Plan rejestracyjny",
            monthly_price=Decimal("99.00"),
            daily_price=Decimal("4.00"),
            yearly_price=Decimal("990.00"),
            limits_json={"cpu_cores": 1.5, "ram_mb": 1536},
            is_active=True,
        )
        db.session.add(plan)
        db.session.commit()
        plan_id = plan.id

    response = client.post(
        "/auth/register",
        data={
            "first_name": "Anna",
            "last_name": "Nowak",
            "username": "anna_register",
            "email": "anna@example.test",
            "password": "StrongPass1!",
            "password_confirm": "StrongPass1!",
            "plan_id": plan_id,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/client" in response.headers.get("Location", "")

    with app.app_context():
        user = User.query.filter_by(username="anna_register").first()
        assert user is not None
        assert user.client_profile is not None
        client_profile = user.client_profile
        assert client_profile.resource_limits.get("selected_plan_id") == plan_id
        assert Decimal(str(client_profile.balance.balance)) == Decimal("0.00")
        hosting_service = (
            ClientService.query.filter_by(client_id=client_profile.id, service_type="hosting")
            .order_by(ClientService.created_at.desc())
            .first()
        )
        assert hosting_service is not None
        assert hosting_service.service_plan_id == plan_id


def test_client_apache_resource_limits_follow_selected_plan(app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        plan = ServicePlan(
            name="Resource Plan",
            code="resource-plan",
            monthly_price=Decimal("59.00"),
            daily_price=Decimal("2.00"),
            yearly_price=Decimal("590.00"),
            limits_json={"cpu_cores": 2, "ram_mb": 2048},
            is_active=True,
        )
        service = ClientService(
            client=client_profile,
            plan=plan,
            name="Hosting Resource",
            service_type="hosting",
            status="active",
            starts_on=date.today(),
            billing_period="monthly",
            recurring_amount=Decimal("59.00"),
            auto_suspend=True,
            auto_resume=True,
        )
        db.session.add_all([plan, service])
        db.session.commit()

        limits = client_apache_resource_limits(client_profile)
        assert limits["cpus"] == "2"
        assert limits["memory"] == "2048m"


def test_login_accepts_short_password_input_without_form_validation_error(client):
    response = client.post(
        "/auth/login",
        data={"username": "admin", "password": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Nieprawid" in response.get_data(as_text=True)


def test_login_remember_me_sets_remember_cookie(client):
    response = client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!", "remember_me": "y"},
        follow_redirects=False,
    )
    cookies = " ".join(response.headers.getlist("Set-Cookie"))
    assert response.status_code == 302
    assert "remember_token=" in cookies


def test_login_requires_2fa_for_user_with_enabled_2fa(client, app):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        assert user is not None
        secret = generate_two_factor_secret()
        user.two_factor_enabled = True
        user.two_factor_method = "totp"
        user.two_factor_secret = secret
        db.session.commit()

    login_response = client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    assert login_response.headers.get("Location", "").startswith("/auth/2fa")

    verify_response = client.post(
        "/auth/2fa",
        data={"code": current_totp(secret)},
        follow_redirects=False,
    )
    assert verify_response.status_code == 302
    assert verify_response.headers.get("Location", "").startswith("/admin/")


def test_login_rejects_invalid_2fa_code(client, app):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        assert user is not None
        user.two_factor_enabled = True
        user.two_factor_method = "totp"
        user.two_factor_secret = generate_two_factor_secret()
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )
    response = client.post(
        "/auth/2fa",
        data={"code": "000000"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Nieprawidlowy kod 2FA" in response.get_data(as_text=True)


def test_user_can_enable_and_disable_2fa_in_settings(client, app):
    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )

    response = client.get("/auth/2fa/settings")
    assert response.status_code == 200

    with client.session_transaction() as session_data:
        setup_secret = session_data.get("two_factor_setup_secret")
    assert setup_secret

    enable_response = client.post(
        "/auth/2fa/settings",
        data={
            "totp-code": current_totp(setup_secret),
            "totp-submit": "1",
        },
        follow_redirects=True,
    )
    assert enable_response.status_code == 200
    assert "zostalo wlaczone" in enable_response.get_data(as_text=True)

    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        assert user.two_factor_enabled is True
        assert user.two_factor_method == "totp"
        user_secret = user.two_factor_secret

    disable_response = client.post(
        "/auth/2fa/settings",
        data={
            "disable-password": "Client123!",
            "disable-code": current_totp(user_secret),
            "disable-submit": "1",
        },
        follow_redirects=True,
    )
    assert disable_response.status_code == 200
    assert "zostalo wylaczone" in disable_response.get_data(as_text=True)

    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        assert user.two_factor_enabled is False
        assert user.two_factor_secret is None


def test_login_supports_email_2fa(client, app):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        assert user is not None
        user.two_factor_enabled = True
        user.two_factor_method = "email"
        user.two_factor_secret = None
        db.session.commit()

    login_response = client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302
    assert login_response.headers.get("Location", "").startswith("/auth/2fa")

    with client.session_transaction() as session_data:
        email_code = session_data.get("pending_2fa_email_code_test")
    assert email_code

    verify_response = client.post(
        "/auth/2fa",
        data={"code": email_code},
        follow_redirects=False,
    )
    assert verify_response.status_code == 302
    assert verify_response.headers.get("Location", "").startswith("/admin/")


def test_user_can_enable_email_2fa_in_settings(client, app):
    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    response = client.post(
        "/auth/2fa/settings",
        data={
            "email-password": "Client123!",
            "email-submit": "1",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "przez e-mail zostalo wlaczone" in response.get_data(as_text=True)

    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        assert user.two_factor_enabled is True
        assert user.two_factor_method == "email"
        assert user.two_factor_secret is None


def test_login_rejects_external_next_redirect(client):
    response = client.post(
        "/auth/login?next=https://evil.example/phish",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    location = response.headers.get("Location", "")
    assert location.startswith("/admin/")
    assert "evil.example" not in location


def test_admin_dashboard_loads(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "Ostatnie logi operacji" in response.get_data(as_text=True)


def test_operator_can_open_admin_dashboard(client):
    client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "Ostatnie logi operacji" in response.get_data(as_text=True)


def test_client_and_operator_can_communicate_via_tickets(client, app):
    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    create_response = client.post(
        "/client/tickets/new",
        data={
            "subject": "Problem z domena",
            "category": "hosting",
            "priority": "high",
            "message": "Po zmianie DNS strona nie dziala.",
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200
    assert "Ticket zostal utworzony" in create_response.get_data(as_text=True)

    with app.app_context():
        ticket = Ticket.query.order_by(Ticket.id.desc()).first()
        assert ticket is not None
        ticket_id = ticket.id
        assert ticket.status == "open"
        assert TicketMessage.query.filter_by(ticket_id=ticket.id).count() == 1

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=True,
    )
    reply_response = client.post(
        f"/admin/tickets/{ticket_id}",
        data={
            "reply-message": "Dziekujemy, sprawdzamy konfiguracje DNS po naszej stronie.",
            "reply-submit": "1",
        },
        follow_redirects=True,
    )
    assert reply_response.status_code == 200
    assert "Odpowiedz zostala wyslana" in reply_response.get_data(as_text=True)

    with app.app_context():
        ticket = Ticket.query.get(ticket_id)
        assert ticket is not None
        assert ticket.status == "answered"
        messages = TicketMessage.query.filter_by(ticket_id=ticket.id).order_by(TicketMessage.id.asc()).all()
        assert len(messages) == 2
        assert messages[0].author.username == "client"
        assert messages[1].author.username == "operator"


def test_ticket_notifications_send_to_staff_and_client(client, app, monkeypatch):
    app.config["TICKETS_EMAIL_NOTIFICATIONS_ENABLED"] = True
    sent_messages: list[dict[str, str]] = []

    def fake_send_plain_email(*, to_email: str, subject: str, body: str):
        sent_messages.append({"to": to_email, "subject": subject, "body": body})
        return None

    monkeypatch.setattr("panel.services.ticket_notifications.send_plain_email", fake_send_plain_email)

    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    client.post(
        "/client/tickets/new",
        data={
            "subject": "Awaria SMTP",
            "category": "mail",
            "priority": "normal",
            "message": "Wiadomosci nie wychodza od rana.",
        },
        follow_redirects=True,
    )

    with app.app_context():
        ticket = Ticket.query.order_by(Ticket.id.desc()).first()
        assert ticket is not None
        ticket_id = ticket.id

    staff_recipients = {item["to"] for item in sent_messages}
    assert "admin@test.local" in staff_recipients
    assert "operator@test.local" in staff_recipients

    sent_messages.clear()
    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=True,
    )
    client.post(
        f"/admin/tickets/{ticket_id}",
        data={
            "reply-message": "Sprawdzilismy logi i przywrocilismy kolejke.",
            "reply-submit": "1",
        },
        follow_redirects=True,
    )

    assert any(item["to"] == "client@test.local" for item in sent_messages)


def test_admin_smoke_test_page_loads(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/smoke-test")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Smoketest aplikacji" in body
    assert "Uruchom smoketest" in body


def test_admin_can_run_smoke_test_from_panel(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post("/admin/smoke-test", data={}, follow_redirects=True)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Smoketest zakonczony:" in body
    assert "PASS" in body


def test_smoke_test_json_requires_valid_token(client):
    response = client.get("/monitoring/smoke-test.json")
    assert response.status_code == 403


def test_smoke_test_json_returns_payload_for_valid_token(client):
    response = client.get("/monitoring/smoke-test.json", headers={"X-Smoke-Test-Token": "test-smoke-token"})
    payload = response.get_json()
    assert response.status_code == 200
    assert payload is not None
    assert payload["source"] == "http"
    assert payload["total"] >= 1
    assert isinstance(payload["checks"], list)


def test_smoke_test_json_rejects_query_param_token(client):
    response = client.get("/monitoring/smoke-test.json?token=test-smoke-token")
    assert response.status_code == 403


def test_smoke_test_json_rejects_ip_outside_allowlist(client, app):
    app.config["SMOKE_TEST_API_ALLOWLIST"] = "10.99.0.0/16"
    response = client.get(
        "/monitoring/smoke-test.json",
        headers={"X-Smoke-Test-Token": "test-smoke-token"},
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert response.status_code == 403


def test_admin_panel_is_blocked_outside_local_network(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/", environ_overrides={"REMOTE_ADDR": "8.8.8.8"})
    assert response.status_code == 403


def test_admin_mail_page_loads(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/mail")
    assert response.status_code == 200
    assert "Skrzynki" in response.get_data(as_text=True)


def test_admin_can_create_mailbox(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        domain = Domain(
            client=client_profile,
            name="example.test",
            document_root="/var/www/example",
            php_version="8.3",
            status="active",
        )
        db.session.add(domain)
        db.session.commit()
        client_id = client_profile.id
        domain_id = domain.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post(
        "/admin/mail/mailboxes/new",
        data={
            "client_id": client_id,
            "domain_id": domain_id,
            "email": "mailbox@example.test",
            "password": "StrongPass1!",
            "quota_mb": 1024,
            "status": "active",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        mailbox = Mailbox.query.filter_by(email="mailbox@example.test").first()
        assert mailbox is not None


def test_admin_ssl_page_loads(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/ssl")
    assert response.status_code == 200
    assert "Certyfikaty SSL" in response.get_data(as_text=True)


def test_admin_ssl_edit_page_loads_for_domain_certificate(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        domain = Domain(
            client=client_profile,
            name="ssl-example.test",
            document_root="/var/www/ssl-example",
            php_version="8.3",
            status="active",
        )
        cert = SSLCertificate(
            domain=domain,
            common_name="ssl-example.test",
            provider="manual",
            status="active",
        )
        db.session.add_all([domain, cert])
        db.session.commit()
        cert_id = cert.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get(f"/admin/ssl/{cert_id}/edit")
    assert response.status_code == 200
    assert "ssl-example.test" in response.get_data(as_text=True)


def test_admin_domain_create_provisions_expected_directory_tree(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        client_id = client_profile.id
        username = client_profile.user.username
        clients_root = Path(app.config["CLIENT_HOME_ROOT"])

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post(
        "/admin/domains/new",
        data={
            "client_id": client_id,
            "client_service_id": 0,
            "name": "example.test",
            "document_root": "",
            "php_version": "8.3",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    domain_root = clients_root / username / "domains" / "example.test"
    assert (domain_root / "public").is_dir()
    assert (domain_root / "private").is_dir()
    assert (domain_root / "subdomains").is_dir()
    assert (domain_root / "ssl").is_dir()
    assert (domain_root / "config").is_dir()
    assert (domain_root / "public" / ".htaccess").exists()
    assert (domain_root / "config" / "domain.json").exists()

    with app.app_context():
        domain = Domain.query.filter_by(name="example.test").first()
        assert domain is not None
        assert domain.document_root == str(domain_root / "public")


def test_admin_domain_create_rejects_invalid_domain_name(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        client_id = client_profile.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post(
        "/admin/domains/new",
        data={
            "client_id": client_id,
            "client_service_id": 0,
            "name": "bad\nname.example",
            "document_root": "",
            "php_version": "8.3",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    with app.app_context():
        domain = Domain.query.filter_by(name="bad\nname.example").first()
        assert domain is None


def test_admin_subdomain_create_provisions_directory_tree(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        domain = Domain(
            client=client_profile,
            name="example.test",
            document_root="/tmp/placeholder",
            php_version="8.3",
            status="active",
        )
        db.session.add(domain)
        db.session.commit()
        domain_id = domain.id
        username = client_profile.user.username
        clients_root = Path(app.config["CLIENT_HOME_ROOT"])

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post(
        f"/admin/domains/{domain_id}/subdomains/new",
        data={
            "name": "blog",
            "document_root": "",
            "php_version": "8.3",
            "status": "active",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    subdomain_root = clients_root / username / "domains" / "example.test" / "subdomains" / "blog"
    assert (subdomain_root / "public").is_dir()
    assert (subdomain_root / "private").is_dir()
    assert (subdomain_root / "ssl").is_dir()
    assert (subdomain_root / "config").is_dir()

    with app.app_context():
        subdomain = Subdomain.query.filter_by(name="blog").first()
        assert subdomain is not None
        assert subdomain.document_root == str(subdomain_root / "public")


def test_login_get_is_not_rate_limited(client):
    for _ in range(12):
        response = client.get("/auth/login")
        assert response.status_code == 200


def test_login_rate_limit_renders_custom_429(app):
    app.config["LOGIN_RATELIMIT"] = "3 per minute"
    test_client = app.test_client()
    for _ in range(3):
        response = test_client.post(
            "/auth/login",
            data={"username": "admin", "password": "wrong-password"},
            follow_redirects=False,
        )
        assert response.status_code in {200, 302}

    blocked = test_client.post(
        "/auth/login",
        data={"username": "admin", "password": "wrong-password"},
        follow_redirects=False,
    )
    body = blocked.get_data(as_text=True)
    assert blocked.status_code == 429
    assert "Za duzo prob" in body
    assert "Zbyt wiele prob logowania" in body


def test_client_cannot_open_admin_panel(client):
    client.post("/auth/login", data={"username": "client", "password": "Client123!"}, follow_redirects=True)
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 403


def test_client_can_create_database_user_with_prefixed_login(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        database = HostingDatabase(
            client=client_profile,
            name="client_data_db",
            engine="mariadb",
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            status="active",
        )
        db.session.add(database)
        db.session.commit()
        database_id = database.id
        expected_prefix = f"{client_profile.user.username}_"

    client.post("/auth/login", data={"username": "client", "password": "Client123!"}, follow_redirects=True)
    response = client.post(
        f"/client/databases/{database_id}/users/new",
        data={
            "database_id": database_id,
            "username": "app",
            "password": "StrongPass1!",
            "host": "localhost",
            "privileges": ["SELECT", "INSERT"],
            "status": "active",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        db_user = DatabaseUser.query.filter_by(database_id=database_id).first()
        assert db_user is not None
        assert db_user.username == f"{expected_prefix}app"
        assert db_user.privileges == ["SELECT", "INSERT"]


def test_client_can_manage_database_user_privileges(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        database = HostingDatabase(
            client=client_profile,
            name="client_reporting_db",
            engine="mariadb",
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            status="active",
        )
        db_user = DatabaseUser(
            database=database,
            username=f"{client_profile.user.username}_report",
            host="localhost",
            status="active",
            privileges=["SELECT"],
        )
        db_user.set_password("StrongPass1!")
        db.session.add_all([database, db_user])
        db.session.commit()
        db_user_id = db_user.id
        database_id = database.id

    client.post("/auth/login", data={"username": "client", "password": "Client123!"}, follow_redirects=True)
    response = client.post(
        f"/client/databases/users/{db_user_id}/edit",
        data={
            "database_id": database_id,
            "username": "report",
            "password": "",
            "host": "127.0.0.1",
            "privileges": ["SELECT", "UPDATE", "DELETE"],
            "status": "disabled",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        updated = DatabaseUser.query.get(db_user_id)
        assert updated is not None
        assert updated.username == "client_report"
        assert updated.host == "127.0.0.1"
        assert updated.status == "disabled"
        assert updated.privileges == ["SELECT", "UPDATE", "DELETE"]


def test_balance_adjustment_creates_transaction(app):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        client_profile = Client.query.first()
        adjust_balance(client_profile, Decimal("25.00"), "topup", "Test top-up", actor=user)
        db.session.commit()
        tx = BillingTransaction.query.order_by(BillingTransaction.id.desc()).first()
        assert tx.amount == Decimal("25.00")
        assert client_profile.balance.balance == Decimal("75.00")


def test_client_can_topup_balance_with_mock_provider(client, app):
    app.config["ONLINE_PAYMENTS_ENABLED"] = True
    app.config["ONLINE_PAYMENTS_PROVIDER"] = "mock"

    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    response = client.post(
        "/client/billing/topup",
        data={"amount": "25.00"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Platnosc testowa zostala zaksiegowana" in response.get_data(as_text=True)

    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        assert client_profile.balance.balance == Decimal("75.00")
        payment = OnlinePayment.query.order_by(OnlinePayment.id.desc()).first()
        assert payment is not None
        assert payment.status == "completed"


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


def test_smoke_test_cli_command(app):
    runner = app.test_cli_runner()
    result = runner.invoke(args=["smoke-test"])
    assert result.exit_code == 0
    assert "Smoketest: OK" in result.output
    assert "[PASS]" in result.output


def test_ticket_create_with_attachment_applies_sla(client, app):
    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    response = client.post(
        "/client/tickets/new",
        data={
            "subject": "Blad 500 po deployu",
            "category": "hosting",
            "priority": "high",
            "message": "Po deployu aplikacja zwraca 500.",
            "attachment": (io.BytesIO(b"traceback"), "error.log"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Ticket zostal utworzony" in response.get_data(as_text=True)

    with app.app_context():
        ticket = Ticket.query.order_by(Ticket.id.desc()).first()
        assert ticket is not None
        assert ticket.first_response_due_at is not None
        assert ticket.first_response_at is None
        attachment = TicketAttachment.query.filter_by(ticket_id=ticket.id).first()
        assert attachment is not None
        root = Path(app.config["STORAGE_ROOT"]) / "ticket_attachments"
        assert (root / attachment.storage_path).is_file()


def test_staff_reply_marks_first_response_with_attachment(client, app):
    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    client.post(
        "/client/tickets/new",
        data={
            "subject": "Problem z SSL",
            "category": "hosting",
            "priority": "normal",
            "message": "Certyfikat sie nie odnawia.",
        },
        follow_redirects=True,
    )

    with app.app_context():
        ticket = Ticket.query.order_by(Ticket.id.desc()).first()
        assert ticket is not None
        ticket_id = ticket.id

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=True,
    )
    response = client.post(
        f"/admin/tickets/{ticket_id}",
        data={
            "reply-message": "Naprawione i zweryfikowane.",
            "reply-submit": "1",
            "attachment": (io.BytesIO(b"fixed"), "fix.log"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        ticket = Ticket.query.get(ticket_id)
        assert ticket is not None
        assert ticket.first_response_at is not None
        assert TicketAttachment.query.filter_by(ticket_id=ticket_id).count() >= 1


def test_client_plan_change_with_proration(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        basic = ServicePlan(
            name="Basic Hosting",
            code="basic-hosting",
            monthly_price=Decimal("30.00"),
            daily_price=Decimal("1.00"),
            yearly_price=Decimal("300.00"),
            limits_json={"cpu_cores": 1, "ram_mb": 1024},
            is_active=True,
        )
        pro = ServicePlan(
            name="Pro Hosting",
            code="pro-hosting",
            monthly_price=Decimal("60.00"),
            daily_price=Decimal("2.00"),
            yearly_price=Decimal("600.00"),
            limits_json={"cpu_cores": 2, "ram_mb": 2048},
            is_active=True,
        )
        service = ClientService(
            client=client_profile,
            plan=basic,
            name="Hosting WWW",
            service_type="hosting",
            status="active",
            starts_on=date.today(),
            billing_period="monthly",
            recurring_amount=Decimal("30.00"),
            auto_suspend=True,
            auto_resume=True,
        )
        db.session.add_all([basic, pro, service])
        db.session.commit()
        service_id = service.id
        pro_id = pro.id

    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    response = client.post(
        f"/client/billing/services/{service_id}/plan-change",
        data={
            f"plan-{service_id}-target_plan_id": str(pro_id),
            f"plan-{service_id}-submit": "1",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        service = ClientService.query.get(service_id)
        assert service is not None
        assert service.service_plan_id == pro_id
        client_profile = Client.query.first()
        assert client_profile is not None
        assert client_profile.balance.balance == Decimal("20.00")
        tx = BillingTransaction.query.filter_by(transaction_type="plan_change_proration").first()
        assert tx is not None


def test_client_can_request_backup_restore(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        backup_root = Path(app.config["BACKUP_ROOT"])
        backup_root.mkdir(parents=True, exist_ok=True)
        dump_path = backup_root / "db-backup.sql"
        dump_path.write_text("-- sample", encoding="utf-8")
        backup = Backup(
            client=client_profile,
            backup_type="database",
            status="completed",
            storage_path="db-backup.sql",
        )
        db.session.add(backup)
        db.session.commit()
        backup_id = backup.id

    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    response = client.post(
        f"/client/backups/{backup_id}/restore",
        data={},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        job = BackupRestoreJob.query.order_by(BackupRestoreJob.id.desc()).first()
        assert job is not None
        assert job.backup_id == backup_id
        assert job.status == "queued"


def test_api_token_allows_api_ticket_flow(client, app):
    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        token, plain_token = issue_api_token(user=user, name="Test API")
        db.session.add(token)
        db.session.commit()

    me_response = client.get(
        "/api/v1/me",
        headers={"Authorization": f"Bearer {plain_token}"},
    )
    assert me_response.status_code == 200
    assert me_response.get_json()["username"] == "client"

    create_ticket_response = client.post(
        "/api/v1/tickets",
        json={
            "subject": "API: niedostepna strona",
            "message": "Prosze sprawdzic status aplikacji.",
            "category": "hosting",
            "priority": "normal",
        },
        headers={"Authorization": f"Bearer {plain_token}"},
    )
    assert create_ticket_response.status_code == 201

    with app.app_context():
        token = ApiToken.query.filter_by(name="Test API").first()
        assert token is not None
        assert token.last_used_at is not None
        ticket = Ticket.query.order_by(Ticket.id.desc()).first()
        assert ticket is not None
        assert (ticket.metadata_json or {}).get("source") == "api"


def test_admin_can_send_webhook_test_delivery(client, app, monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def getcode(self):
            return 200

        def read(self):
            return b"ok"

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _FakeResponse())

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    create_response = client.post(
        "/admin/webhooks/new",
        data={
            "name": "Test endpoint",
            "target_url": "https://example.test/webhooks",
            "secret": "secret",
            "client_id": 0,
            "event_types": ["ticket.created"],
            "is_active": "y",
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200

    with app.app_context():
        endpoint = WebhookEndpoint.query.filter_by(name="Test endpoint").first()
        assert endpoint is not None
        endpoint_id = endpoint.id

    send_response = client.post(
        f"/admin/webhooks/{endpoint_id}/send-test",
        data={},
        follow_redirects=True,
    )
    assert send_response.status_code == 200

    with app.app_context():
        delivery = WebhookDelivery.query.order_by(WebhookDelivery.id.desc()).first()
        assert delivery is not None
        assert delivery.endpoint_id == endpoint_id
        assert delivery.success is True


def test_client_monitoring_page_loads(client):
    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    response = client.get("/client/monitoring")
    assert response.status_code == 200
    assert "Zuzycie zasobow" in response.get_data(as_text=True)


def test_admin_monitoring_clients_page_loads(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/monitoring/clients")
    assert response.status_code == 200
    assert "Monitoring klientow" in response.get_data(as_text=True)


def test_financial_enforcement_grace_suspend_and_auto_unsuspend(app):
    with app.app_context():
        actor = User.query.filter_by(username="admin").first()
        client_profile = Client.query.first()
        assert actor is not None
        assert client_profile is not None

        service = ClientService(
            client=client_profile,
            name="Hosting Finance",
            service_type="hosting",
            status="active",
            starts_on=date.today(),
            billing_period="monthly",
            recurring_amount=Decimal("20.00"),
            auto_suspend=True,
            auto_resume=True,
        )
        db.session.add(service)
        db.session.flush()

        db.session.add(
            BillingCycle(
                client_service=service,
                cycle_type="monthly",
                amount=Decimal("20.00"),
                due_date=date.today() - timedelta(days=5),
                status="overdue",
            )
        )
        client_profile.balance.balance = Decimal("-20.00")

        result = update_client_financial_status_for_date(client_profile, actor=actor, as_of=date.today())
        assert result["service_transitions"] >= 1
        assert service.status == "suspended"
        assert client_profile.billing_status == "suspended_non_payment"
        assert client_profile.user.status == "suspended_financial"
        assert (
            AccountSuspension.query.filter_by(
                client_service_id=service.id,
                suspension_type="financial",
                active=True,
            ).count()
            == 1
        )

        adjust_balance(
            client_profile,
            Decimal("50.00"),
            "topup_manual",
            "Test auto-unsuspend",
            actor=actor,
        )
        db.session.commit()

        db.session.refresh(service)
        db.session.refresh(client_profile)
        assert service.status == "active"
        assert client_profile.billing_status == "current"
        assert client_profile.user.status == "active"
        assert BillingCycle.query.filter_by(client_service_id=service.id, status="overdue").count() == 0
        assert (
            AccountSuspension.query.filter_by(
                client_service_id=service.id,
                suspension_type="financial",
                active=True,
            ).count()
            == 0
        )


def test_financial_enforcement_uses_plan_grace_override(app):
    with app.app_context():
        actor = User.query.filter_by(username="admin").first()
        client_profile = Client.query.first()
        assert actor is not None
        assert client_profile is not None

        plan = ServicePlan(
            name="Grace Plan",
            code="grace-plan",
            monthly_price=Decimal("30.00"),
            daily_price=Decimal("1.00"),
            yearly_price=Decimal("300.00"),
            grace_days_override=7,
            limits_json={},
            is_active=True,
        )
        service = ClientService(
            client=client_profile,
            plan=plan,
            name="Hosting Grace",
            service_type="hosting",
            status="active",
            starts_on=date.today(),
            billing_period="monthly",
            recurring_amount=Decimal("30.00"),
            auto_suspend=True,
            auto_resume=True,
        )
        db.session.add_all([plan, service])
        db.session.flush()

        due_date = date.today() - timedelta(days=4)
        db.session.add(
            BillingCycle(
                client_service=service,
                cycle_type="monthly",
                amount=Decimal("30.00"),
                due_date=due_date,
                status="overdue",
            )
        )
        client_profile.balance.balance = Decimal("-5.00")

        update_client_financial_status_for_date(client_profile, actor=actor, as_of=date.today())
        assert service.status == "pending_payment"
        assert client_profile.billing_status == "in_grace_period"

        update_client_financial_status_for_date(client_profile, actor=actor, as_of=due_date + timedelta(days=8))
        assert service.status == "suspended"
        assert client_profile.billing_status == "suspended_non_payment"


def test_financial_enforcement_manual_override_prevents_auto_suspend_and_unsuspend(app):
    with app.app_context():
        actor = User.query.filter_by(username="admin").first()
        client_profile = Client.query.first()
        assert actor is not None
        assert client_profile is not None

        service = ClientService(
            client=client_profile,
            name="Hosting Override",
            service_type="hosting",
            status="active",
            starts_on=date.today(),
            billing_period="monthly",
            recurring_amount=Decimal("25.00"),
            auto_suspend=True,
            auto_resume=True,
            financial_enforcement_override=True,
        )
        db.session.add(service)
        db.session.flush()

        db.session.add(
            BillingCycle(
                client_service=service,
                cycle_type="monthly",
                amount=Decimal("25.00"),
                due_date=date.today() - timedelta(days=10),
                status="overdue",
            )
        )
        client_profile.balance.balance = Decimal("-10.00")

        update_client_financial_status_for_date(client_profile, actor=actor, as_of=date.today())
        assert service.status == "active"
        assert client_profile.billing_status == "overdue"

        service.status = "suspended"
        client_profile.balance.balance = Decimal("20.00")
        update_client_financial_status_for_date(client_profile, actor=actor, as_of=date.today())
        assert service.status == "suspended"
