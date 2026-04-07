from __future__ import annotations

import base64
from datetime import date, datetime, timedelta
from decimal import Decimal
import io
from pathlib import Path

import pytest

from panel.extensions import db
from panel.models import (
    AccountSuspension,
    ActivityLog,
    ApprovalRequest,
    AutomationExecution,
    AutomationRule,
    BillingCycle,
    BillingTransaction,
    BulkOperation,
    ClientOnboardingState,
    Client,
    ClientService,
    ClientSSHKey,
    ComplianceResult,
    ComplianceRun,
    DataLegalHold,
    DisasterRecoveryCheckRun,
    DisasterRecoveryProfile,
    DomainRegistration,
    EventStreamEntry,
    ExportJob,
    ApiToken,
    Backup,
    BackupRestoreJob,
    DatabaseUser,
    Domain,
    HostingDatabase,
    Mailbox,
    MigrationJob,
    OnlinePayment,
    OperatorPermission,
    OverdueReminder,
    PolicyDocument,
    PolicyEvaluation,
    RegistrationFraudCheck,
    RetentionCleanupRun,
    Role,
    SSLCertificate,
    ServicePlan,
    Subdomain,
    SystemSetting,
    TenantRetentionPolicy,
    Ticket,
    TicketAttachment,
    TicketMacro,
    TicketMacroUsage,
    TicketMessage,
    TwoFactorBackupCode,
    User,
    UserSession,
    VaultSecretVersion,
    WebhookDelivery,
    WebhookEndpoint,
)
from panel.seed import seed_defaults
from panel.services.account_security import generate_backup_codes, hash_session_token, issue_user_session
from panel.services.overdue_reminders import send_overdue_reminders
from panel.services.audit import log_activity, verify_activity_chain
from panel.services.client_apache import client_apache_resource_limits
from panel.services.api_tokens import issue_api_token
from panel.services.billing import adjust_balance, update_client_financial_status_for_date
from panel.services.compliance import link_checklist_evidence, run_compliance_checks, upsert_checklist_item
from panel.services.dr_readiness import run_dr_readiness_checks, run_failover_simulation
from panel.services.operator_permissions import domain_choices
from panel.services.policy_engine import activate_policy, rollback_policy
from panel.services.retention import create_legal_hold, run_retention_cleanup, upsert_client_policy
from panel.services.secrets_vault import create_secret, reveal_secret_value, rotate_secret
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


def test_high_risk_registration_is_blocked_by_anti_fraud(client, app):
    with app.app_context():
        plan = ServicePlan(
            name="Anti Fraud Plan",
            code="anti-fraud-plan",
            description="Plan testowy",
            monthly_price=Decimal("29.00"),
            daily_price=Decimal("1.00"),
            yearly_price=Decimal("290.00"),
            limits_json={"cpu_cores": 1, "ram_mb": 1024},
            is_active=True,
        )
        db.session.add(plan)
        db.session.commit()
        plan_id = plan.id

    response = client.post(
        "/auth/register",
        data={
            "first_name": "Temp",
            "last_name": "Temp",
            "username": "spam_bot_99123",
            "email": "temp@mailinator.com",
            "password": "StrongPass1!",
            "password_confirm": "StrongPass1!",
            "plan_id": plan_id,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/auth/login" in response.headers.get("Location", "")

    with app.app_context():
        user = User.query.filter_by(username="spam_bot_99123").first()
        assert user is not None
        assert user.is_active_account is False
        assert user.status == "inactive"
        assert "anti-fraud" in (user.manual_lock_reason or "").lower()

        check = RegistrationFraudCheck.query.filter_by(user_id=user.id).first()
        assert check is not None
        assert check.blocked is True
        assert check.risk_level == "high"


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


def test_login_accepts_backup_code_for_2fa(client, app):
    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        assert user is not None
        user.two_factor_enabled = True
        user.two_factor_method = "totp"
        user.two_factor_secret = generate_two_factor_secret()
        backup_codes = generate_backup_codes(user=user, count=5)
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
        data={"code": backup_codes[0]},
        follow_redirects=False,
    )
    assert verify_response.status_code == 302
    assert verify_response.headers.get("Location", "").startswith("/admin/")

    with app.app_context():
        user = User.query.filter_by(username="admin").first()
        assert user is not None
        remaining = (
            TwoFactorBackupCode.query.filter_by(user_id=user.id).filter(TwoFactorBackupCode.used_at.is_(None)).count()
        )
        assert remaining == 4


def test_login_creates_tracked_session_and_logout_revokes_it(client, app):
    login_response = client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    with client.session_transaction() as session_data:
        session_token = session_data.get("auth_session_token")
    assert session_token

    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        active_session = UserSession.query.filter_by(user_id=user.id).filter(UserSession.revoked_at.is_(None)).first()
        assert active_session is not None
        active_session_id = active_session.id

    logout_response = client.get("/auth/logout", follow_redirects=False)
    assert logout_response.status_code == 302

    with app.app_context():
        revoked_session = UserSession.query.get(active_session_id)
        assert revoked_session is not None
        assert revoked_session.revoked_at is not None


def test_user_can_revoke_other_session(client, app):
    first_login = client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=False,
    )
    assert first_login.status_code == 302

    with client.session_transaction() as session_data:
        current_plain_token = session_data.get("auth_session_token")
    assert current_plain_token
    current_hash = hash_session_token(current_plain_token)
    assert current_hash is not None

    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        _, other_session = issue_user_session(
            user=user,
            ip_address="127.0.0.2",
            user_agent="pytest-other-session",
        )
        db.session.commit()
        sessions = (
            UserSession.query.filter_by(user_id=user.id)
            .filter(UserSession.revoked_at.is_(None))
            .all()
        )
        assert len(sessions) >= 2
        target_session_id = next(
            row.id for row in sessions if row.session_token_hash != current_hash and row.revoked_at is None
        )
        assert target_session_id == other_session.id

    revoke_response = client.post(
        f"/auth/sessions/{target_session_id}/revoke",
        follow_redirects=True,
    )
    assert revoke_response.status_code == 200
    assert "Sesja zostala cofnieta" in revoke_response.get_data(as_text=True)

    with app.app_context():
        revoked_session = UserSession.query.get(target_session_id)
        assert revoked_session is not None
        assert revoked_session.revoked_at is not None


def test_logout_all_sessions_keeps_current(client, app):
    first_login = client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=False,
    )
    assert first_login.status_code == 302

    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        issue_user_session(
            user=user,
            ip_address="127.0.0.2",
            user_agent="pytest-other-session",
        )
        db.session.commit()

    logout_all_response = client.post("/auth/sessions/logout-all", follow_redirects=True)
    assert logout_all_response.status_code == 200
    assert "Cofnieto sesji" in logout_all_response.get_data(as_text=True)

    with client.session_transaction() as session_data:
        current_plain_token = session_data.get("auth_session_token")
    assert current_plain_token
    current_hash = hash_session_token(current_plain_token)
    assert current_hash is not None

    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        active_sessions = UserSession.query.filter_by(user_id=user.id).filter(UserSession.revoked_at.is_(None)).all()
        assert len(active_sessions) == 1
        assert active_sessions[0].session_token_hash == current_hash


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
        backup_codes_count = (
            TwoFactorBackupCode.query.filter_by(user_id=user.id).filter(TwoFactorBackupCode.used_at.is_(None)).count()
        )
        assert backup_codes_count == 10

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
        backup_codes_count = TwoFactorBackupCode.query.filter_by(user_id=user.id).count()
        assert backup_codes_count == 0


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


def test_operator_without_custom_permissions_keeps_legacy_access(client):
    login_response = client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    plans_response = client.get("/admin/billing/plans", follow_redirects=False)
    assert plans_response.status_code == 200


def test_operator_custom_permissions_can_block_billing_read(client, app):
    with app.app_context():
        operator = User.query.filter_by(username="operator").first()
        assert operator is not None
        db.session.add(
            OperatorPermission(
                user_id=operator.id,
                domain="billing",
                can_read=False,
                can_write=False,
            )
        )
        db.session.commit()

    login_response = client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    plans_response = client.get("/admin/billing/plans", follow_redirects=False)
    assert plans_response.status_code == 403


def test_operator_custom_permissions_can_block_billing_write(client, app):
    with app.app_context():
        operator = User.query.filter_by(username="operator").first()
        assert operator is not None
        db.session.add(
            OperatorPermission(
                user_id=operator.id,
                domain="billing",
                can_read=True,
                can_write=False,
            )
        )
        db.session.commit()

    login_response = client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=False,
    )
    assert login_response.status_code == 302

    read_response = client.get("/admin/billing/plans", follow_redirects=False)
    assert read_response.status_code == 200

    write_response = client.post(
        "/admin/billing/plans/new",
        data={},
        follow_redirects=False,
    )
    assert write_response.status_code == 403


def test_admin_can_manage_operator_permissions_matrix(client, app):
    with app.app_context():
        operator = User.query.filter_by(username="operator").first()
        assert operator is not None
        operator_id = operator.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )

    get_response = client.get(f"/admin/users/{operator_id}/permissions", follow_redirects=False)
    assert get_response.status_code == 200

    payload = {"granular_enabled": "1", "billing_can_read": "on"}
    post_response = client.post(
        f"/admin/users/{operator_id}/permissions",
        data=payload,
        follow_redirects=False,
    )
    assert post_response.status_code == 302

    with app.app_context():
        rows = OperatorPermission.query.filter_by(user_id=operator_id).all()
        assert len(rows) == len(domain_choices())
        billing_row = next((row for row in rows if row.domain == "billing"), None)
        assert billing_row is not None
        assert billing_row.can_read is True
        assert billing_row.can_write is False


def test_client_can_create_and_cancel_migration_job(client, app):
    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=False,
    )

    create_response = client.post(
        "/client/migrations",
        data={
            "source_provider": "cpanel",
            "source_hostname": "old-host.example.com",
            "source_username": "legacy-user",
            "source_password": "Secret123!",
            "source_path": "/home/legacy/public_html",
            "notes": "Prosze przeniesc wszystkie dane.",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 302

    with app.app_context():
        client_user = User.query.filter_by(username="client").first()
        assert client_user is not None
        job = MigrationJob.query.filter_by(client_id=client_user.client_profile.id).first()
        assert job is not None
        assert job.status == "queued"
        job_id = job.id

    cancel_response = client.post(
        f"/client/migrations/{job_id}/cancel",
        data={"reason": "Zmiana decyzji"},
        follow_redirects=False,
    )
    assert cancel_response.status_code == 302

    with app.app_context():
        job = MigrationJob.query.get(job_id)
        assert job is not None
        assert job.status == "cancelled"


def test_admin_can_process_migration_jobs_to_completion(client, app):
    with app.app_context():
        requested_by = User.query.filter_by(username="client").first()
        assert requested_by is not None
        job = MigrationJob(
            client=requested_by.client_profile,
            requested_by=requested_by,
            source_provider="cpanel",
            status="queued",
            current_step="preflight",
            progress_percent=0,
            masked_summary="CPANEL | old-host.example.com | l***r",
        )
        db.session.add(job)
        db.session.commit()
        job_id = job.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )

    for _ in range(5):
        process_response = client.post("/admin/migrations/process", follow_redirects=False)
        assert process_response.status_code == 302

    with app.app_context():
        job = MigrationJob.query.get(job_id)
        assert job is not None
        assert job.status == "completed"
        assert job.progress_percent == 100
        assert job.current_step == "done"


def test_admin_can_create_and_trigger_automation_rule(client, app):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )

    create_response = client.post(
        "/admin/automations/new",
        data={
            "name": "Migration Queue Alarm",
            "description": "Log alert when queue processed",
            "trigger_event": "migration.queue_processed",
            "conditions_json": "{}",
            "actions_json": '[{"type":"log","message":"Queue processed"}]',
            "stop_on_match": "y",
            "is_active": "y",
        },
        follow_redirects=False,
    )
    assert create_response.status_code == 302

    trigger_response = client.post(
        "/admin/automations",
        data={
            "trigger_event": "migration.queue_processed",
            "payload_json": '{"processed": 3}',
        },
        follow_redirects=False,
    )
    assert trigger_response.status_code == 302

    with app.app_context():
        rule = AutomationRule.query.filter_by(name="Migration Queue Alarm").first()
        assert rule is not None
        execution = AutomationExecution.query.filter_by(rule_id=rule.id).first()
        assert execution is not None
        assert execution.status == "success"


def test_admin_reports_page_shows_financial_metrics(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )
    response = client.get("/admin/reports", follow_redirects=False)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "MRR" in body
    assert "ARPU" in body
    assert "Churn" in body
    assert "Overdue" in body
    assert "Prognoza 3M" in body
    assert "Przypomnienia 30d" in body


def test_send_overdue_reminders_creates_deduplicated_log_entries(app):
    app.config["BILLING_OVERDUE_REMINDER_OFFSETS"] = "0"
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        service = ClientService(
            client=client_profile,
            name="Overdue Reminder Service",
            service_type="hosting",
            status="pending_payment",
            starts_on=date.today() - timedelta(days=35),
            billing_period="monthly",
            recurring_amount=Decimal("49.00"),
            auto_suspend=True,
            auto_resume=True,
        )
        cycle = BillingCycle(
            client_service=service,
            cycle_type="monthly",
            amount=Decimal("49.00"),
            due_date=date.today(),
            status="overdue",
        )
        db.session.add_all([service, cycle])
        db.session.commit()

        first_summary = send_overdue_reminders(as_of=date.today())
        db.session.commit()
        second_summary = send_overdue_reminders(as_of=date.today())
        db.session.commit()

        reminders = OverdueReminder.query.filter_by(billing_cycle_id=cycle.id).all()
        assert first_summary["sent"] >= 1
        assert second_summary["sent"] == 0
        assert len(reminders) == 1
        assert reminders[0].status == "sent"


def test_admin_can_trigger_overdue_reminders_from_reports(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post("/admin/reports/reminders/send", data={}, follow_redirects=False)
    assert response.status_code == 302


def test_admin_can_review_and_unlock_anti_fraud_alert(client, app):
    with app.app_context():
        role = Role.query.filter_by(name="client").first()
        assert role is not None
        user = User(
            role=role,
            username="fraud_locked_user",
            email="fraud-locked@example.test",
            first_name="Fraud",
            last_name="Locked",
            status="inactive",
            is_active_account=False,
            manual_lock_reason="Automatyczna blokada: wysoki wynik anti-fraud",
        )
        user.set_password("StrongPass1!")
        profile = Client(user=user, company_name="Fraud Co")
        check = RegistrationFraudCheck(
            user=user,
            username=user.username,
            email=user.email,
            ip_address="127.0.0.1",
            score=92,
            risk_level="high",
            blocked=True,
            reasons_json=["Adres e-mail nalezy do domeny tymczasowej."],
        )
        db.session.add_all([user, profile, check])
        db.session.commit()
        check_id = check.id
        user_id = user.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )

    page_response = client.get("/admin/security/anti-fraud", follow_redirects=False)
    assert page_response.status_code == 200
    assert "fraud_locked_user" in page_response.get_data(as_text=True)

    review_response = client.post(
        f"/admin/security/anti-fraud/{check_id}/review",
        data={"note": "Manualny przeglad"},
        follow_redirects=False,
    )
    assert review_response.status_code == 302

    unlock_response = client.post(
        f"/admin/security/anti-fraud/{check_id}/unlock",
        data={},
        follow_redirects=False,
    )
    assert unlock_response.status_code == 302

    with app.app_context():
        user = User.query.get(user_id)
        assert user is not None
        assert user.is_active_account is True
        assert user.status == "active"

        check = RegistrationFraudCheck.query.get(check_id)
        assert check is not None
        assert check.reviewed_at is not None


def test_admin_can_export_clients_csv_and_log_job(client, app):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )
    response = client.get("/admin/exports/clients?format=csv", follow_redirects=False)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "client_id" in body
    assert "username" in body

    with app.app_context():
        job = ExportJob.query.order_by(ExportJob.id.desc()).first()
        assert job is not None
        assert job.dataset == "clients"
        assert job.format == "csv"
        assert job.status == "completed"
        assert job.row_count >= 1


def test_admin_can_export_tickets_xlsx(client, app):
    with app.app_context():
        client_user = User.query.filter_by(username="client").first()
        assert client_user is not None
        ticket = Ticket(
            client=client_user.client_profile,
            created_by=client_user,
            subject="Export ticket",
            category="hosting",
            priority="normal",
            status="open",
        )
        db.session.add(ticket)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=False,
    )
    response = client.get("/admin/exports/tickets?format=xlsx", follow_redirects=False)
    assert response.status_code == 200
    assert "spreadsheetml.sheet" in (response.content_type or "")

    with app.app_context():
        job = ExportJob.query.order_by(ExportJob.id.desc()).first()
        assert job is not None
        assert job.dataset == "tickets"
        assert job.format == "xlsx"


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


def test_admin_can_create_ticket_macro(client, app):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post(
        "/admin/tickets/macros/new",
        data={
            "name": "Billing Reminder",
            "category": "billing",
            "visibility_scope": "all_staff",
            "subject_template": "Przypomnienie o platnosci",
            "body_template": "Witaj {{client_full_name}}, numer ticketu: {{ticket_id}}.",
            "sort_order": "10",
            "is_active": "y",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Makro zostalo utworzone" in response.get_data(as_text=True)

    with app.app_context():
        macro = TicketMacro.query.filter_by(name="Billing Reminder").first()
        assert macro is not None
        assert macro.category == "billing"
        assert macro.is_active is True


def test_operator_reply_can_use_ticket_macro_and_track_usage(client, app):
    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()
        assert admin_user is not None
        macro = TicketMacro(
            name="Support Greeting",
            category="technical_support",
            visibility_scope="all_staff",
            body_template="Witaj {{client_full_name}}, pracujemy nad zgloszeniem {{ticket_id}}.",
            sort_order=1,
            is_active=True,
            created_by=admin_user,
            updated_by=admin_user,
        )
        db.session.add(macro)
        db.session.commit()
        macro_id = macro.id

    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    create_response = client.post(
        "/client/tickets/new",
        data={
            "subject": "Problem z SSL",
            "category": "hosting",
            "priority": "normal",
            "message": "Prosze o pomoc.",
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200

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
    preview_response = client.get(f"/admin/tickets/{ticket_id}/macro-preview?macro_id={macro_id}")
    preview_payload = preview_response.get_json()
    assert preview_response.status_code == 200
    assert preview_payload is not None
    assert preview_payload.get("ok") is True
    assert "TKT-" in (preview_payload.get("rendered") or "")

    reply_response = client.post(
        f"/admin/tickets/{ticket_id}",
        data={
            "reply-macro_id": str(macro_id),
            "reply-message": "Dodatkowa notatka od operatora.",
            "reply-submit": "1",
        },
        follow_redirects=True,
    )
    assert reply_response.status_code == 200
    assert "Odpowiedz zostala wyslana" in reply_response.get_data(as_text=True)

    with app.app_context():
        latest_message = TicketMessage.query.filter_by(ticket_id=ticket_id).order_by(TicketMessage.id.desc()).first()
        assert latest_message is not None
        assert "Dodatkowa notatka od operatora" in latest_message.message
        assert "TKT-" in latest_message.message
        usage = TicketMacroUsage.query.filter_by(ticket_id=ticket_id, macro_id=macro_id).first()
        assert usage is not None
        assert usage.ticket_message_id == latest_message.id


def test_operator_can_reply_with_macro_only_without_custom_message(client, app):
    with app.app_context():
        admin_user = User.query.filter_by(username="admin").first()
        assert admin_user is not None
        macro = TicketMacro(
            name="Macro Only",
            category="technical_support",
            visibility_scope="all_staff",
            body_template="Witaj {{client_full_name}}, ticket {{ticket_id}} jest obslugiwany.",
            sort_order=2,
            is_active=True,
            created_by=admin_user,
            updated_by=admin_user,
        )
        db.session.add(macro)
        db.session.commit()
        macro_id = macro.id

    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    create_response = client.post(
        "/client/tickets/new",
        data={
            "subject": "Makro bez tresci",
            "category": "hosting",
            "priority": "normal",
            "message": "Prosze o aktualizacje statusu.",
        },
        follow_redirects=True,
    )
    assert create_response.status_code == 200

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
    reply_response = client.post(
        f"/admin/tickets/{ticket_id}",
        data={
            "reply-macro_id": str(macro_id),
            "reply-message": "",
            "reply-submit": "1",
        },
        follow_redirects=True,
    )
    assert reply_response.status_code == 200
    assert "Odpowiedz zostala wyslana" in reply_response.get_data(as_text=True)

    with app.app_context():
        latest_message = TicketMessage.query.filter_by(ticket_id=ticket_id).order_by(TicketMessage.id.desc()).first()
        assert latest_message is not None
        assert "ticket" in latest_message.message.lower()
        assert "TKT-" in latest_message.message


def test_admin_bulk_service_plan_change_supports_preview_and_execute(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        old_plan = ServicePlan(
            name="Bulk Old Plan",
            code="bulk-old-plan",
            monthly_price=Decimal("39.00"),
            daily_price=Decimal("1.50"),
            yearly_price=Decimal("390.00"),
            is_active=True,
        )
        new_plan = ServicePlan(
            name="Bulk New Plan",
            code="bulk-new-plan",
            monthly_price=Decimal("59.00"),
            daily_price=Decimal("2.00"),
            yearly_price=Decimal("590.00"),
            is_active=True,
        )
        service = ClientService(
            client=client_profile,
            plan=old_plan,
            name="Bulk Hosting",
            service_type="hosting",
            status="active",
            starts_on=date.today(),
            billing_period="monthly",
            recurring_amount=Decimal("39.00"),
            auto_suspend=True,
            auto_resume=True,
        )
        db.session.add_all([old_plan, new_plan, service])
        db.session.commit()
        service_id = service.id
        new_plan_id = new_plan.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )

    dry_run_response = client.post(
        "/admin/billing/services/bulk",
        data={
            "action": "plan_change",
            "service_ids": [str(service_id)],
            "target_plan_id": str(new_plan_id),
            "dry_run": "1",
        },
        follow_redirects=False,
    )
    assert dry_run_response.status_code == 302

    with app.app_context():
        service = ClientService.query.get(service_id)
        assert service is not None
        assert service.service_plan_id != new_plan_id

    execute_response = client.post(
        "/admin/billing/services/bulk",
        data={
            "action": "plan_change",
            "service_ids": [str(service_id)],
            "target_plan_id": str(new_plan_id),
            "confirm_text": "POTWIERDZ",
            "dry_run": "0",
        },
        follow_redirects=False,
    )
    assert execute_response.status_code == 302

    with app.app_context():
        service = ClientService.query.get(service_id)
        assert service is not None
        assert service.service_plan_id == new_plan_id
        operation = BulkOperation.query.order_by(BulkOperation.id.desc()).first()
        assert operation is not None
        assert operation.operation_type == "service_plan_change"


def test_admin_bulk_user_lock_supports_preview_and_execute(client, app):
    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        user_id = user.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )

    dry_run_response = client.post(
        "/admin/users/bulk-lock",
        data={
            "action": "lock",
            "user_ids": [str(user_id)],
            "reason": "Test lock",
            "dry_run": "1",
        },
        follow_redirects=False,
    )
    assert dry_run_response.status_code == 302

    with app.app_context():
        user = User.query.get(user_id)
        assert user is not None
        assert user.is_active_account is True
        assert user.status == "active"

    execute_response = client.post(
        "/admin/users/bulk-lock",
        data={
            "action": "lock",
            "user_ids": [str(user_id)],
            "reason": "Test lock",
            "confirm_text": "POTWIERDZ",
            "dry_run": "0",
        },
        follow_redirects=False,
    )
    assert execute_response.status_code == 302

    with app.app_context():
        user = User.query.get(user_id)
        assert user is not None
        assert user.is_active_account is False
        assert user.status == "inactive"


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


def test_admin_can_register_and_renew_domain_in_registrar(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        domain = Domain(
            client=client_profile,
            name="registrar-example.test",
            document_root="/tmp/registrar-example/public",
            php_version="8.3",
            status="active",
        )
        db.session.add(domain)
        db.session.commit()
        domain_id = domain.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )

    register_response = client.post(
        f"/admin/domains/{domain_id}/registrar/register",
        data={"years": "1", "auto_renew": "1"},
        follow_redirects=False,
    )
    assert register_response.status_code == 302

    with app.app_context():
        registration = DomainRegistration.query.filter_by(domain_id=domain_id).first()
        assert registration is not None
        assert registration.status == "active"
        assert registration.expires_on is not None
        previous_expiry = registration.expires_on

    renew_response = client.post(
        f"/admin/domains/{domain_id}/registrar/renew",
        data={"years": "2"},
        follow_redirects=False,
    )
    assert renew_response.status_code == 302

    sync_response = client.post(
        f"/admin/domains/{domain_id}/registrar/sync",
        data={},
        follow_redirects=False,
    )
    assert sync_response.status_code == 302

    with app.app_context():
        registration = DomainRegistration.query.filter_by(domain_id=domain_id).first()
        assert registration is not None
        assert registration.expires_on is not None
        assert registration.expires_on > previous_expiry
        assert registration.last_synced_at is not None


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


def test_domain_delete_requires_approval_and_executes_after_second_user_approval(client, app):
    app.config["APPROVALS_ENABLED"] = True
    app.config["APPROVALS_RISKY_ACTIONS"] = "domains.delete"
    app.config["APPROVALS_REQUIRED_COUNTS"] = "domains.delete=1"
    app.config["APPROVALS_ALLOW_SELF_APPROVAL"] = False

    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        domain = Domain(
            client=client_profile,
            name="approval-delete.example.test",
            document_root="/var/www/approval-delete",
            php_version="8.3",
            status="active",
            is_primary=False,
        )
        db.session.add(domain)
        db.session.commit()
        domain_id = domain.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )

    delete_response = client.post(
        f"/admin/domains/{domain_id}/delete",
        data={},
        follow_redirects=False,
    )
    assert delete_response.status_code == 302

    with app.app_context():
        domain = Domain.query.get(domain_id)
        assert domain is not None
        approval = ApprovalRequest.query.filter_by(action_key="domains.delete", target_id=str(domain_id)).first()
        assert approval is not None
        assert approval.status == "pending"
        approval_id = approval.id

    self_approve_response = client.post(
        f"/admin/security/approvals/{approval_id}/approve",
        data={},
        follow_redirects=True,
    )
    assert self_approve_response.status_code == 200
    assert "Self-approval" in self_approve_response.get_data(as_text=True)

    with app.app_context():
        approval = ApprovalRequest.query.get(approval_id)
        assert approval is not None
        assert approval.status == "pending"

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=True,
    )

    approve_response = client.post(
        f"/admin/security/approvals/{approval_id}/approve",
        data={},
        follow_redirects=False,
    )
    assert approve_response.status_code == 302

    with app.app_context():
        approval = ApprovalRequest.query.get(approval_id)
        assert approval is not None
        assert approval.status == "executed"
        assert Domain.query.get(domain_id) is None


def test_admin_restore_requires_approval_and_executes_after_approval(client, app):
    app.config["APPROVALS_ENABLED"] = True
    app.config["APPROVALS_RISKY_ACTIONS"] = "backups.restore"
    app.config["APPROVALS_REQUIRED_COUNTS"] = "backups.restore=1"
    app.config["APPROVALS_ALLOW_SELF_APPROVAL"] = False

    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None

        backup_root = Path(app.config["BACKUP_ROOT"])
        source_dir = backup_root / "approval-restore-src"
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "index.txt").write_text("ok", encoding="utf-8")

        backup = Backup(
            client=client_profile,
            backup_type="files",
            status="completed",
            storage_path=str(source_dir),
        )
        db.session.add(backup)
        db.session.commit()
        backup_id = backup.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )

    response = client.post(
        f"/admin/backups/{backup_id}/restore",
        data={},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        approval = ApprovalRequest.query.filter_by(action_key="backups.restore", target_id=str(backup_id)).first()
        assert approval is not None
        assert approval.status == "pending"
        assert BackupRestoreJob.query.filter_by(backup_id=backup_id).count() == 0
        approval_id = approval.id

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": "operator", "password": "Operator123!"},
        follow_redirects=True,
    )
    approve_response = client.post(
        f"/admin/security/approvals/{approval_id}/approve",
        data={},
        follow_redirects=False,
    )
    assert approve_response.status_code == 302

    with app.app_context():
        approval = ApprovalRequest.query.get(approval_id)
        assert approval is not None
        assert approval.status == "executed"
        job = BackupRestoreJob.query.filter_by(backup_id=backup_id).order_by(BackupRestoreJob.id.desc()).first()
        assert job is not None
        assert job.status in {"completed", "queued"}


def test_audit_chain_detects_tampering(app):
    with app.app_context():
        actor = User.query.filter_by(username="admin").first()
        assert actor is not None

        log_activity(
            "test.audit_chain.first",
            "audit_test",
            "Pierwszy wpis chain",
            entity_id="audit-1",
            actor=actor,
            metadata={"step": 1},
        )
        log_activity(
            "test.audit_chain.second",
            "audit_test",
            "Drugi wpis chain",
            entity_id="audit-2",
            actor=actor,
            metadata={"step": 2},
        )
        db.session.commit()

        result_ok = verify_activity_chain(max_errors=20)
        assert result_ok["valid"] is True
        assert result_ok["checked"] >= 2

        row = (
            ActivityLog.query.filter_by(action="test.audit_chain.second")
            .order_by(ActivityLog.id.desc())
            .first()
        )
        assert row is not None
        row.description = "Zmieniona tresc"
        db.session.commit()

        result_broken = verify_activity_chain(max_errors=20)
        assert result_broken["valid"] is False
        assert any(item.get("type") == "hash_mismatch" for item in result_broken["errors"])


def test_client_can_manage_ssh_keys_and_sync_authorized_keys(client, app):
    with app.app_context():
        client_user = User.query.filter_by(username="client").first()
        assert client_user is not None
        client_profile = client_user.client_profile
        assert client_profile is not None
        client_id = client_profile.id

    key_payload = base64.b64encode(b"pytest-ssh-key-material-0001").decode("ascii")
    public_key = f"ssh-ed25519 {key_payload} pytest@local"

    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )

    add_response = client.post(
        "/client/ssh-keys/add",
        data={"label": "Pytest key", "public_key": public_key},
        follow_redirects=True,
    )
    assert add_response.status_code == 200
    assert "Klucz SSH zostal dodany" in add_response.get_data(as_text=True)

    with app.app_context():
        row = ClientSSHKey.query.filter_by(client_id=client_id).first()
        assert row is not None
        assert row.status == "active"
        assert row.fingerprint_sha256.startswith("SHA256:")
        key_id = row.id
        username = row.client.user.username if row.client and row.client.user else f"client-{row.client_id}"

        authorized_keys_path = Path(app.config["CLIENT_HOME_ROOT"]) / username / ".ssh" / "authorized_keys"
        assert authorized_keys_path.exists()
        content = authorized_keys_path.read_text(encoding="utf-8")
        assert "ssh-ed25519" in content

    toggle_response = client.post(
        f"/client/ssh-keys/{key_id}/toggle",
        data={},
        follow_redirects=True,
    )
    assert toggle_response.status_code == 200

    with app.app_context():
        row = ClientSSHKey.query.get(key_id)
        assert row is not None
        assert row.status == "disabled"
        username = row.client.user.username if row.client and row.client.user else f"client-{row.client_id}"
        authorized_keys_path = Path(app.config["CLIENT_HOME_ROOT"]) / username / ".ssh" / "authorized_keys"
        assert authorized_keys_path.read_text(encoding="utf-8") == ""

    client.get("/auth/logout", follow_redirects=True)
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    admin_page = client.get(f"/admin/ssh-keys?client_id={client_id}", follow_redirects=False)
    assert admin_page.status_code == 200
    assert "Pytest key" in admin_page.get_data(as_text=True)

    delete_response = client.post(
        f"/admin/ssh-keys/{key_id}/delete",
        data={},
        follow_redirects=True,
    )
    assert delete_response.status_code == 200

    with app.app_context():
        assert ClientSSHKey.query.get(key_id) is None


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


def test_admin_can_toggle_financial_override_for_service(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        service = ClientService(
            client=client_profile,
            name="Toggle Override",
            service_type="hosting",
            status="active",
            starts_on=date.today(),
            billing_period="monthly",
            recurring_amount=Decimal("19.00"),
            auto_suspend=True,
            auto_resume=True,
            financial_enforcement_override=False,
        )
        db.session.add(service)
        db.session.commit()
        service_id = service.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post(
        f"/admin/billing/services/{service_id}/financial-override",
        data={"enabled": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    with app.app_context():
        service = ClientService.query.get(service_id)
        assert service is not None
        assert service.financial_enforcement_override is True


def test_admin_can_manual_suspend_and_unsuspend_service(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        service = ClientService(
            client=client_profile,
            name="Manual Suspend",
            service_type="hosting",
            status="active",
            starts_on=date.today(),
            billing_period="monthly",
            recurring_amount=Decimal("22.00"),
            auto_suspend=True,
            auto_resume=True,
        )
        db.session.add(service)
        db.session.commit()
        service_id = service.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    suspend_response = client.post(
        f"/admin/billing/services/{service_id}/manual-suspend",
        data={"reason": "Test manual suspend"},
        follow_redirects=True,
    )
    assert suspend_response.status_code == 200

    with app.app_context():
        service = ClientService.query.get(service_id)
        assert service is not None
        assert service.status == "blocked_manual"

    unsuspend_response = client.post(
        f"/admin/billing/services/{service_id}/manual-unsuspend",
        data={},
        follow_redirects=True,
    )
    assert unsuspend_response.status_code == 200

    with app.app_context():
        service = ClientService.query.get(service_id)
        assert service is not None
        assert service.status == "active"


def test_admin_billing_services_page_shows_financial_state(client):
    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.get("/admin/billing/services")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Uslugi klientow" in body


def test_client_billing_page_shows_financial_state(client):
    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )
    response = client.get("/client/billing")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Stan egzekucji finansowej" in body


def test_retention_cleanup_respects_legal_hold_and_run_key(app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None

        upsert_client_policy(
            client_id=client_profile.id,
            resource_type="event_stream_entries",
            anonymize_after_days=0,
            delete_after_days=0,
            legal_hold_enabled=True,
            is_active=True,
            notes="pytest retention",
        )

        entry = EventStreamEntry(
            client=client_profile,
            event_type="pytest.retention",
            category="system",
            severity="info",
            source="pytest",
            message="Retention candidate",
            event_at=datetime.utcnow() - timedelta(days=3),
            payload_json={"sample": True},
        )
        db.session.add(entry)
        db.session.flush()
        entry_id = entry.id

        hold = create_legal_hold(
            client_id=client_profile.id,
            resource_type="event_stream_entries",
            resource_id=str(entry_id),
            reason="pytest hold",
            created_by=None,
            expires_at=None,
        )
        db.session.flush()
        hold_id = hold.id
        db.session.commit()

        first = run_retention_cleanup(run_key="pytest-retention-a", triggered_by=None, client_id=client_profile.id)
        db.session.commit()
        assert first["idempotent"] is False
        assert EventStreamEntry.query.get(entry_id) is not None

        hold = DataLegalHold.query.get(hold_id)
        assert hold is not None
        hold.status = "released"
        hold.released_at = datetime.utcnow()
        db.session.commit()

        second = run_retention_cleanup(run_key="pytest-retention-b", triggered_by=None, client_id=client_profile.id)
        db.session.commit()
        assert second["idempotent"] is False
        assert EventStreamEntry.query.get(entry_id) is None

        third = run_retention_cleanup(run_key="pytest-retention-b", triggered_by=None, client_id=client_profile.id)
        assert third["idempotent"] is True


def test_secrets_vault_create_rotate_and_reveal(app):
    pytest.importorskip("cryptography")

    with app.app_context():
        client_profile = Client.query.first()
        admin = User.query.filter_by(username="admin").first()
        assert client_profile is not None
        assert admin is not None

        secret = create_secret(
            client=client_profile,
            name="pytest-secret",
            secret_type="api_key",
            plain_value="initial-value-123",
            created_by=admin,
            rotation_interval_days=30,
            description="pytest secret",
        )
        db.session.commit()

        current_version = VaultSecretVersion.query.filter_by(secret_id=secret.id, is_current=True).first()
        assert current_version is not None
        assert "initial-value-123" not in current_version.value_encrypted

        revealed = reveal_secret_value(secret, revealed_by=admin)
        assert revealed == "initial-value-123"

        with pytest.raises(ValueError):
            reveal_secret_value(secret, revealed_by=admin)

        rotate_secret(secret=secret, plain_value="rotated-value-456", rotated_by=admin, reason="pytest-rotation")
        db.session.commit()

        revealed_after_rotate = reveal_secret_value(secret, revealed_by=admin)
        assert revealed_after_rotate == "rotated-value-456"


def test_activity_log_emits_event_stream_entry(app):
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        assert admin is not None

        log_activity(
            "test.event_stream_emit",
            "test_entity",
            "Seed event stream from audit log",
            actor=admin,
        )
        db.session.commit()

        row = (
            EventStreamEntry.query.filter_by(event_type="activity.test.event_stream_emit")
            .order_by(EventStreamEntry.id.desc())
            .first()
        )
        assert row is not None
        assert row.category == "system"


def test_policy_blocks_domain_delete_when_enforced(client, app):
    app.config["APPROVALS_ENABLED"] = True
    app.config["APPROVALS_RISKY_ACTIONS"] = "domains.delete"
    app.config["APPROVALS_REQUIRED_COUNTS"] = "domains.delete=1"

    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None

        policy = PolicyDocument(
            name="Block domains delete without approval",
            scope="tenant",
            client=client_profile,
            version="v1",
            enforcement_mode="enforce",
            is_active=True,
            definition_json={
                "rules": [
                    {
                        "event": "domains.delete.request",
                        "when": {
                            "all": [
                                {"field": "requires_approval", "operator": "eq", "value": True},
                                {"field": "approval_granted", "operator": "eq", "value": False},
                            ]
                        },
                        "effect": "deny",
                        "message": "Delete blocked until approval granted",
                    }
                ]
            },
        )

        domain = Domain(
            client=client_profile,
            name="policy-delete-block.example.test",
            document_root="/var/www/policy-delete-block",
            php_version="8.3",
            status="active",
            is_primary=False,
        )
        db.session.add_all([policy, domain])
        db.session.commit()
        domain_id = domain.id

    client.post(
        "/auth/login",
        data={"username": "admin", "password": "Admin123!"},
        follow_redirects=True,
    )
    response = client.post(
        f"/admin/domains/{domain_id}/delete",
        data={},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "zablokowane przez policy" in response.get_data(as_text=True)

    with app.app_context():
        assert Domain.query.get(domain_id) is not None
        evaluation = (
            PolicyEvaluation.query.filter_by(event_type="domains.delete.request", decision="deny")
            .order_by(PolicyEvaluation.id.desc())
            .first()
        )
        assert evaluation is not None


def test_client_onboarding_page_and_step_update(client, app):
    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None

        domain = Domain(
            client=client_profile,
            name="onboarding-step.example.test",
            document_root="/var/www/onboarding-step",
            php_version="8.3",
            status="active",
            is_primary=False,
        )
        db.session.add(domain)
        db.session.commit()

    client.post(
        "/auth/login",
        data={"username": "client", "password": "Client123!"},
        follow_redirects=True,
    )

    page = client.get("/client/onboarding")
    assert page.status_code == 200
    assert "Onboarding" in page.get_data(as_text=True)

    update = client.post(
        "/client/onboarding",
        data={"step_id": "connect_domain", "action": "complete"},
        follow_redirects=True,
    )
    assert update.status_code == 200

    with app.app_context():
        client_profile = Client.query.first()
        assert client_profile is not None
        state = ClientOnboardingState.query.filter_by(client_id=client_profile.id).first()
        assert state is not None
        assert "connect_domain" in (state.completed_steps_json or [])


def test_compliance_and_dr_checks_create_records(app):
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        assert admin is not None

        run = run_compliance_checks(actor=admin, client_id=None)
        summary = run_dr_readiness_checks(actor=admin, client_id=None)
        db.session.commit()

        assert ComplianceRun.query.get(run.id) is not None
        assert ComplianceResult.query.filter_by(run_id=run.id).count() >= 1
        assert summary["clients"] >= 1
        assert DisasterRecoveryCheckRun.query.count() >= 1


def test_dr_failover_simulation_creates_run(app):
    with app.app_context():
        client_profile = Client.query.first()
        admin = User.query.filter_by(username="admin").first()
        assert client_profile is not None
        assert admin is not None

        summary = run_failover_simulation(client=client_profile, actor=admin, safe_mode=True)
        db.session.commit()

        assert summary["safe_mode"] is True
        assert summary["result"] in {"passed", "failed"}
        row = DisasterRecoveryCheckRun.query.get(summary["run_id"])
        assert row is not None
        details = dict(row.details_json or {})
        assert details.get("run_type") == "failover_simulation"


def test_api_events_endpoint_with_events_scope(client, app):
    with app.app_context():
        user = User.query.filter_by(username="client").first()
        assert user is not None
        token, plain_token = issue_api_token(user=user, name="Events API", scopes=["events:read"])
        db.session.add(token)

        log_activity(
            "test.api.events",
            "test_entity",
            "Seed API events endpoint",
            actor=user,
            client=user.client_profile,
        )
        db.session.commit()

    response = client.get(
        "/api/v1/events",
        headers={"Authorization": f"Bearer {plain_token}"},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload.get("events"), list)
    assert len(payload["events"]) >= 1


def test_policy_lifecycle_activate_and_rollback(app):
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        client_profile = Client.query.first()
        assert admin is not None
        assert client_profile is not None

        p1 = PolicyDocument(
            name="Lifecycle Policy A",
            scope="tenant",
            client=client_profile,
            version="v1",
            enforcement_mode="enforce",
            is_active=False,
            definition_json={
                "rules": [
                    {
                        "event": "domains.delete.request",
                        "effect": "deny",
                        "message": "A",
                    }
                ]
            },
            created_by=admin,
            updated_by=admin,
        )
        p2 = PolicyDocument(
            name="Lifecycle Policy B",
            scope="tenant",
            client=client_profile,
            version="v2",
            enforcement_mode="enforce",
            is_active=False,
            definition_json={
                "rules": [
                    {
                        "event": "domains.delete.request",
                        "effect": "deny",
                        "message": "B",
                    }
                ]
            },
            created_by=admin,
            updated_by=admin,
        )
        db.session.add_all([p1, p2])
        db.session.flush()

        activate_policy(p1, actor=admin)
        assert p1.is_active is True

        activate_policy(p2, actor=admin)
        assert p2.is_active is True
        assert p1.is_active is False

        rolled_back = rollback_policy(p2, actor=admin)
        assert rolled_back.id == p1.id
        assert p1.is_active is True
        assert p2.is_active is False


def test_compliance_evidence_link_enforces_tenant_scope(app):
    with app.app_context():
        admin = User.query.filter_by(username="admin").first()
        client_profile = Client.query.first()
        client_role = Role.query.filter_by(name="client").first()
        assert admin is not None
        assert client_profile is not None
        assert client_role is not None

        other_user = User(
            role=client_role,
            username="client_scope2",
            email="client_scope2@test.local",
            first_name="Scope",
            last_name="Two",
            status="active",
        )
        other_user.set_password("Client123!")
        other_client = Client(user=other_user, company_name="Scope 2")
        db.session.add_all([other_user, other_client])
        db.session.flush()

        control = upsert_checklist_item(
            client_id=client_profile.id,
            control_code="ac-tenant-scope",
            title="Tenant scope control",
            description="pytest",
            status="in_progress",
            owner=admin,
            due_date=None,
            actor=admin,
        )
        db.session.flush()

        log_activity(
            "test.compliance.scope.same_tenant",
            "test_entity",
            "same-tenant evidence",
            actor=admin,
            client=client_profile,
        )
        db.session.flush()
        same_tenant_event = ActivityLog.query.order_by(ActivityLog.id.desc()).first()
        assert same_tenant_event is not None

        row = link_checklist_evidence(
            checklist_item=control,
            evidence_type="audit_log",
            reference_id=str(same_tenant_event.id),
            actor=admin,
        )
        assert row is not None

        log_activity(
            "test.compliance.scope.other_tenant",
            "test_entity",
            "other-tenant evidence",
            actor=admin,
            client=other_client,
        )
        db.session.flush()
        other_tenant_event = ActivityLog.query.order_by(ActivityLog.id.desc()).first()
        assert other_tenant_event is not None

        with pytest.raises(ValueError):
            link_checklist_evidence(
                checklist_item=control,
                evidence_type="audit_log",
                reference_id=str(other_tenant_event.id),
                actor=admin,
            )
