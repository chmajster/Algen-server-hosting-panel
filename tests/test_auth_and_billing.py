from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from panel.extensions import db
from panel.models import BillingTransaction, Client, DatabaseUser, Domain, HostingDatabase, Mailbox, SSLCertificate, Subdomain, SystemSetting, User
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


def test_admin_dashboard_loads(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/")
    assert response.status_code == 200
    assert "Ostatnie logi operacji" in response.get_data(as_text=True)


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
