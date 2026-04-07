from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from flask_login import UserMixin
from sqlalchemy import Index, UniqueConstraint

from panel.extensions import bcrypt, db, login_manager


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class Role(TimestampMixin, db.Model):
    __tablename__ = "roles"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)

    users = db.relationship("User", back_populates="role", lazy="dynamic")


class User(UserMixin, TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), nullable=False, index=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(120), nullable=False)
    last_name = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="active", index=True)
    is_active_account = db.Column(db.Boolean, nullable=False, default=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    last_login_ip = db.Column(db.String(45), nullable=True)
    manual_lock_reason = db.Column(db.String(255), nullable=True)
    two_factor_enabled = db.Column(db.Boolean, nullable=False, default=False)
    two_factor_method = db.Column(db.String(16), nullable=False, default="totp")
    two_factor_secret = db.Column(db.String(128), nullable=True)

    role = db.relationship("Role", back_populates="users")
    client_profile = db.relationship(
        "Client",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
    status_history = db.relationship(
        "UserStatusHistory",
        back_populates="user",
        foreign_keys="UserStatusHistory.user_id",
        cascade="all, delete-orphan",
        order_by="desc(UserStatusHistory.created_at)",
    )
    activity_logs = db.relationship(
        "ActivityLog",
        back_populates="actor",
        foreign_keys="ActivityLog.actor_user_id",
    )
    created_tickets = db.relationship(
        "Ticket",
        back_populates="created_by",
        foreign_keys="Ticket.created_by_user_id",
    )
    assigned_tickets = db.relationship(
        "Ticket",
        back_populates="assigned_to",
        foreign_keys="Ticket.assigned_to_user_id",
    )
    ticket_messages = db.relationship(
        "TicketMessage",
        back_populates="author",
        foreign_keys="TicketMessage.author_user_id",
    )
    api_tokens = db.relationship(
        "ApiToken",
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="ApiToken.user_id",
    )
    sessions = db.relationship(
        "UserSession",
        back_populates="user",
        cascade="all, delete-orphan",
        order_by="desc(UserSession.created_at)",
    )
    two_factor_backup_codes = db.relationship(
        "TwoFactorBackupCode",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    operator_permissions = db.relationship(
        "OperatorPermission",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    ticket_macro_creations = db.relationship(
        "TicketMacro",
        back_populates="created_by",
        foreign_keys="TicketMacro.created_by_user_id",
    )
    ticket_macro_updates = db.relationship(
        "TicketMacro",
        back_populates="updated_by",
        foreign_keys="TicketMacro.updated_by_user_id",
    )
    ticket_macro_usages = db.relationship(
        "TicketMacroUsage",
        back_populates="used_by",
        foreign_keys="TicketMacroUsage.used_by_user_id",
    )
    bulk_operations = db.relationship(
        "BulkOperation",
        back_populates="initiated_by",
        foreign_keys="BulkOperation.initiated_by_user_id",
    )
    export_jobs = db.relationship(
        "ExportJob",
        back_populates="requested_by",
        foreign_keys="ExportJob.requested_by_user_id",
    )
    fraud_checks = db.relationship(
        "RegistrationFraudCheck",
        back_populates="user",
        foreign_keys="RegistrationFraudCheck.user_id",
    )
    fraud_reviews = db.relationship(
        "RegistrationFraudCheck",
        back_populates="reviewed_by",
        foreign_keys="RegistrationFraudCheck.reviewed_by_user_id",
    )
    requested_approvals = db.relationship(
        "ApprovalRequest",
        back_populates="requested_by",
        foreign_keys="ApprovalRequest.requested_by_user_id",
    )
    executed_approvals = db.relationship(
        "ApprovalRequest",
        back_populates="executed_by",
        foreign_keys="ApprovalRequest.executed_by_user_id",
    )
    approval_decisions = db.relationship(
        "ApprovalDecision",
        back_populates="decided_by",
        foreign_keys="ApprovalDecision.decided_by_user_id",
    )
    created_ssh_keys = db.relationship(
        "ClientSSHKey",
        back_populates="created_by",
        foreign_keys="ClientSSHKey.created_by_user_id",
    )
    created_legal_holds = db.relationship(
        "DataLegalHold",
        back_populates="created_by",
        foreign_keys="DataLegalHold.created_by_user_id",
    )
    retention_cleanup_runs = db.relationship(
        "RetentionCleanupRun",
        back_populates="triggered_by",
        foreign_keys="RetentionCleanupRun.triggered_by_user_id",
    )
    created_vault_secrets = db.relationship(
        "VaultSecret",
        back_populates="created_by",
        foreign_keys="VaultSecret.created_by_user_id",
    )
    updated_vault_secrets = db.relationship(
        "VaultSecret",
        back_populates="updated_by",
        foreign_keys="VaultSecret.updated_by_user_id",
    )
    vault_secret_versions = db.relationship(
        "VaultSecretVersion",
        back_populates="created_by",
        foreign_keys="VaultSecretVersion.created_by_user_id",
    )
    event_entries = db.relationship(
        "EventStreamEntry",
        back_populates="actor",
        foreign_keys="EventStreamEntry.actor_user_id",
    )
    compliance_runs = db.relationship(
        "ComplianceRun",
        back_populates="triggered_by",
        foreign_keys="ComplianceRun.triggered_by_user_id",
    )
    owned_compliance_controls = db.relationship(
        "ComplianceChecklistItem",
        back_populates="owner",
        foreign_keys="ComplianceChecklistItem.owner_user_id",
    )
    compliance_controls_created = db.relationship(
        "ComplianceChecklistItem",
        back_populates="created_by",
        foreign_keys="ComplianceChecklistItem.created_by_user_id",
    )
    compliance_controls_updated = db.relationship(
        "ComplianceChecklistItem",
        back_populates="updated_by",
        foreign_keys="ComplianceChecklistItem.updated_by_user_id",
    )
    compliance_evidence_links = db.relationship(
        "ComplianceEvidenceLink",
        back_populates="linked_by",
        foreign_keys="ComplianceEvidenceLink.linked_by_user_id",
    )
    policy_documents_created = db.relationship(
        "PolicyDocument",
        back_populates="created_by",
        foreign_keys="PolicyDocument.created_by_user_id",
    )
    policy_documents_updated = db.relationship(
        "PolicyDocument",
        back_populates="updated_by",
        foreign_keys="PolicyDocument.updated_by_user_id",
    )
    policy_evaluations = db.relationship(
        "PolicyEvaluation",
        back_populates="evaluated_by",
        foreign_keys="PolicyEvaluation.evaluated_by_user_id",
    )
    onboarding_state_updates = db.relationship(
        "ClientOnboardingState",
        back_populates="updated_by",
        foreign_keys="ClientOnboardingState.updated_by_user_id",
    )
    dr_checks = db.relationship(
        "DisasterRecoveryCheckRun",
        back_populates="checked_by",
        foreign_keys="DisasterRecoveryCheckRun.checked_by_user_id",
    )

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, password)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def has_role(self, role_name: str) -> bool:
        return bool(self.role and self.role.name == role_name)

    def has_any_role(self, *role_names: str) -> bool:
        return bool(self.role and self.role.name in set(role_names))

    @property
    def is_staff(self) -> bool:
        return self.has_any_role("administrator", "operator")


@login_manager.user_loader
def load_user(user_id: str):
    return User.query.get(int(user_id))


class UserStatusHistory(TimestampMixin, db.Model):
    __tablename__ = "user_status_history"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    old_status = db.Column(db.String(32), nullable=True)
    new_status = db.Column(db.String(32), nullable=False)
    reason = db.Column(db.String(255), nullable=True)
    changed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    user = db.relationship("User", foreign_keys=[user_id], back_populates="status_history")
    changed_by = db.relationship("User", foreign_keys=[changed_by_user_id])


class Client(TimestampMixin, db.Model):
    __tablename__ = "clients"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), unique=True, nullable=False, index=True)
    company_name = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(255), nullable=True)
    city = db.Column(db.String(120), nullable=True)
    country = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    resource_limits = db.Column(db.JSON, nullable=False, default=dict)
    allow_dns_management = db.Column(db.Boolean, nullable=False, default=True)
    auto_resume_services = db.Column(db.Boolean, nullable=False, default=True)
    billing_status = db.Column(db.String(32), nullable=False, default="current", index=True)

    user = db.relationship("User", back_populates="client_profile")
    balance = db.relationship(
        "ClientBalance",
        back_populates="client",
        uselist=False,
        cascade="all, delete-orphan",
    )
    services = db.relationship("ClientService", back_populates="client", cascade="all, delete-orphan")
    domains = db.relationship("Domain", back_populates="client", cascade="all, delete-orphan")
    databases = db.relationship("HostingDatabase", back_populates="client", cascade="all, delete-orphan")
    ftp_accounts = db.relationship("FTPAccount", back_populates="client", cascade="all, delete-orphan")
    dns_zones = db.relationship("DNSZone", back_populates="client", cascade="all, delete-orphan")
    mailboxes = db.relationship("Mailbox", back_populates="client", cascade="all, delete-orphan")
    backups = db.relationship("Backup", back_populates="client", cascade="all, delete-orphan")
    online_payments = db.relationship("OnlinePayment", back_populates="client", cascade="all, delete-orphan")
    tickets = db.relationship("Ticket", back_populates="client", cascade="all, delete-orphan")
    resource_samples = db.relationship("ClientResourceSample", back_populates="client", cascade="all, delete-orphan")
    restore_jobs = db.relationship("BackupRestoreJob", back_populates="client", cascade="all, delete-orphan")
    webhook_endpoints = db.relationship("WebhookEndpoint", back_populates="client", cascade="all, delete-orphan")
    resource_alerts = db.relationship("ResourceLimitAlert", back_populates="client", cascade="all, delete-orphan")
    migration_jobs = db.relationship("MigrationJob", back_populates="client", cascade="all, delete-orphan")
    domain_registrations = db.relationship("DomainRegistration", back_populates="client", cascade="all, delete-orphan")
    overdue_reminders = db.relationship("OverdueReminder", back_populates="client", cascade="all, delete-orphan")
    approval_requests = db.relationship("ApprovalRequest", back_populates="client", cascade="all, delete-orphan")
    ssh_keys = db.relationship("ClientSSHKey", back_populates="client", cascade="all, delete-orphan")
    retention_policies = db.relationship("TenantRetentionPolicy", back_populates="client", cascade="all, delete-orphan")
    legal_holds = db.relationship("DataLegalHold", back_populates="client", cascade="all, delete-orphan")
    vault_secrets = db.relationship("VaultSecret", back_populates="client", cascade="all, delete-orphan")
    event_entries = db.relationship("EventStreamEntry", back_populates="client", cascade="all, delete-orphan")
    compliance_runs = db.relationship("ComplianceRun", back_populates="client", cascade="all, delete-orphan")
    compliance_results = db.relationship("ComplianceResult", back_populates="client", cascade="all, delete-orphan")
    compliance_checklist_items = db.relationship(
        "ComplianceChecklistItem",
        back_populates="client",
        cascade="all, delete-orphan",
    )
    compliance_evidence_links = db.relationship(
        "ComplianceEvidenceLink",
        back_populates="client",
        cascade="all, delete-orphan",
    )
    policy_documents = db.relationship("PolicyDocument", back_populates="client", cascade="all, delete-orphan")
    policy_evaluations = db.relationship("PolicyEvaluation", back_populates="client", cascade="all, delete-orphan")
    onboarding_state = db.relationship(
        "ClientOnboardingState",
        back_populates="client",
        uselist=False,
        cascade="all, delete-orphan",
    )
    dr_profile = db.relationship(
        "DisasterRecoveryProfile",
        back_populates="client",
        uselist=False,
        cascade="all, delete-orphan",
    )
    dr_check_runs = db.relationship("DisasterRecoveryCheckRun", back_populates="client", cascade="all, delete-orphan")


class ClientBalance(TimestampMixin, db.Model):
    __tablename__ = "client_balances"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), unique=True, nullable=False, index=True)
    balance = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    currency = db.Column(db.String(8), nullable=False, default="PLN")
    last_recalculated_at = db.Column(db.DateTime, nullable=True)

    client = db.relationship("Client", back_populates="balance")


class BillingTransaction(TimestampMixin, db.Model):
    __tablename__ = "billing_transactions"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    transaction_type = db.Column(db.String(32), nullable=False, index=True)
    description = db.Column(db.String(255), nullable=False)
    balance_after = db.Column(db.Numeric(12, 2), nullable=False)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client")
    actor = db.relationship("User")


class BillingCycle(TimestampMixin, db.Model):
    __tablename__ = "billing_cycles"

    id = db.Column(db.Integer, primary_key=True)
    client_service_id = db.Column(db.Integer, db.ForeignKey("client_services.id"), nullable=False, index=True)
    cycle_type = db.Column(db.String(16), nullable=False, default="monthly")
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    due_date = db.Column(db.Date, nullable=False, index=True)
    last_charged_at = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="scheduled", index=True)

    client_service = db.relationship("ClientService", back_populates="billing_cycles")


class OverdueReminder(TimestampMixin, db.Model):
    __tablename__ = "overdue_reminders"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    client_service_id = db.Column(db.Integer, db.ForeignKey("client_services.id"), nullable=False, index=True)
    billing_cycle_id = db.Column(db.Integer, db.ForeignKey("billing_cycles.id"), nullable=False, index=True)
    reminder_type = db.Column(db.String(32), nullable=False, default="email", index=True)
    day_offset = db.Column(db.Integer, nullable=False, default=0)
    status = db.Column(db.String(32), nullable=False, default="sent", index=True)
    recipient = db.Column(db.String(255), nullable=True)
    subject = db.Column(db.String(255), nullable=True)
    message = db.Column(db.String(500), nullable=True)
    sent_at = db.Column(db.DateTime, nullable=True, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="overdue_reminders")
    client_service = db.relationship("ClientService")
    billing_cycle = db.relationship("BillingCycle")

    __table_args__ = (
        UniqueConstraint("billing_cycle_id", "reminder_type", "day_offset", name="uq_overdue_reminder_cycle_type_day"),
    )


class ServicePlan(TimestampMixin, db.Model):
    __tablename__ = "service_plans"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    monthly_price = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    daily_price = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    yearly_price = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    grace_days_override = db.Column(db.Integer, nullable=True)
    backup_frequency = db.Column(db.String(16), nullable=False, default="daily")
    backup_restore_points = db.Column(db.Integer, nullable=False, default=7)
    backup_retention_days = db.Column(db.Integer, nullable=False, default=30)
    backup_storage_target_id = db.Column(db.Integer, db.ForeignKey("external_backup_targets.id"), nullable=True, index=True)
    limits_json = db.Column(db.JSON, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    services = db.relationship("ClientService", back_populates="plan")
    backup_storage_target = db.relationship("ExternalBackupTarget", back_populates="service_plans")


class ClientService(TimestampMixin, db.Model):
    __tablename__ = "client_services"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    service_plan_id = db.Column(db.Integer, db.ForeignKey("service_plans.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    service_type = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="active", index=True)
    starts_on = db.Column(db.Date, nullable=False, default=date.today)
    ends_on = db.Column(db.Date, nullable=True)
    billing_period = db.Column(db.String(16), nullable=False, default="monthly")
    recurring_amount = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    auto_suspend = db.Column(db.Boolean, nullable=False, default=True)
    auto_resume = db.Column(db.Boolean, nullable=False, default=True)
    financial_enforcement_override = db.Column(db.Boolean, nullable=False, default=False)
    manual_lock_reason = db.Column(db.String(255), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="services")
    plan = db.relationship("ServicePlan", back_populates="services")
    billing_cycles = db.relationship(
        "BillingCycle",
        back_populates="client_service",
        cascade="all, delete-orphan",
    )
    suspensions = db.relationship(
        "AccountSuspension",
        back_populates="client_service",
        cascade="all, delete-orphan",
    )


class AccountSuspension(TimestampMixin, db.Model):
    __tablename__ = "account_suspensions"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    client_service_id = db.Column(db.Integer, db.ForeignKey("client_services.id"), nullable=True, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    suspension_type = db.Column(db.String(32), nullable=False)
    reason = db.Column(db.String(255), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    released_at = db.Column(db.DateTime, nullable=True)

    client = db.relationship("Client")
    client_service = db.relationship("ClientService", back_populates="suspensions")
    actor = db.relationship("User")


class PaymentSetting(TimestampMixin, db.Model):
    __tablename__ = "payment_settings"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    autopay_enabled = db.Column(db.Boolean, nullable=False, default=True)
    default_cycle = db.Column(db.String(16), nullable=False, default="monthly")
    grace_days = db.Column(db.Integer, nullable=False, default=3)
    auto_resume = db.Column(db.Boolean, nullable=False, default=True)

    client = db.relationship("Client")


class OnlinePayment(TimestampMixin, db.Model):
    __tablename__ = "online_payments"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    currency = db.Column(db.String(8), nullable=False, default="PLN")
    provider = db.Column(db.String(32), nullable=False, default="stripe", index=True)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    description = db.Column(db.String(255), nullable=False)
    external_id = db.Column(db.String(191), nullable=True, unique=True, index=True)
    provider_event_id = db.Column(db.String(191), nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="online_payments")
    actor = db.relationship("User")


class Domain(TimestampMixin, db.Model):
    __tablename__ = "domains"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    client_service_id = db.Column(db.Integer, db.ForeignKey("client_services.id"), nullable=True, index=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    document_root = db.Column(db.String(255), nullable=False)
    php_version = db.Column(db.String(16), nullable=False, default="8.3")
    status = db.Column(db.String(32), nullable=False, default="active", index=True)
    is_primary = db.Column(db.Boolean, nullable=False, default=False)
    ssl_enabled = db.Column(db.Boolean, nullable=False, default=False)

    client = db.relationship("Client", back_populates="domains")
    service = db.relationship("ClientService")
    subdomains = db.relationship("Subdomain", back_populates="domain", cascade="all, delete-orphan")
    dns_zone = db.relationship("DNSZone", back_populates="domain", uselist=False)
    ssl_certificate = db.relationship("SSLCertificate", back_populates="domain", uselist=False)
    registration = db.relationship("DomainRegistration", back_populates="domain", uselist=False, cascade="all, delete-orphan")


class Subdomain(TimestampMixin, db.Model):
    __tablename__ = "subdomains"

    id = db.Column(db.Integer, primary_key=True)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    document_root = db.Column(db.String(255), nullable=False)
    php_version = db.Column(db.String(16), nullable=False, default="8.3")
    status = db.Column(db.String(32), nullable=False, default="active", index=True)
    ssl_enabled = db.Column(db.Boolean, nullable=False, default=False)

    domain = db.relationship("Domain", back_populates="subdomains")
    ssl_certificate = db.relationship("SSLCertificate", back_populates="subdomain", uselist=False)

    __table_args__ = (UniqueConstraint("domain_id", "name", name="uq_subdomains_domain_name"),)

    @property
    def full_name(self) -> str:
        return f"{self.name}.{self.domain.name}"


class HostingDatabase(TimestampMixin, db.Model):
    __tablename__ = "databases"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    client_service_id = db.Column(db.Integer, db.ForeignKey("client_services.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False, unique=True, index=True)
    engine = db.Column(db.String(32), nullable=False, default="mariadb")
    charset = db.Column(db.String(32), nullable=False, default="utf8mb4")
    collation = db.Column(db.String(64), nullable=False, default="utf8mb4_unicode_ci")
    status = db.Column(db.String(32), nullable=False, default="active", index=True)

    client = db.relationship("Client", back_populates="databases")
    service = db.relationship("ClientService")
    users = db.relationship("DatabaseUser", back_populates="database", cascade="all, delete-orphan")


class DatabaseUser(TimestampMixin, db.Model):
    __tablename__ = "database_users"

    id = db.Column(db.Integer, primary_key=True)
    database_id = db.Column(db.Integer, db.ForeignKey("databases.id"), nullable=False, index=True)
    username = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    host = db.Column(db.String(120), nullable=False, default="localhost")
    privileges = db.Column(db.JSON, nullable=False, default=list)
    status = db.Column(db.String(32), nullable=False, default="active", index=True)

    database = db.relationship("HostingDatabase", back_populates="users")

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")


class FTPAccount(TimestampMixin, db.Model):
    __tablename__ = "ftp_accounts"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    client_service_id = db.Column(db.Integer, db.ForeignKey("client_services.id"), nullable=True, index=True)
    username = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    home_directory = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    client = db.relationship("Client", back_populates="ftp_accounts")
    service = db.relationship("ClientService")

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")


class DNSZone(TimestampMixin, db.Model):
    __tablename__ = "dns_zones"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), nullable=False, unique=True, index=True)
    name = db.Column(db.String(255), nullable=False, unique=True)
    default_ttl = db.Column(db.Integer, nullable=False, default=3600)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    client = db.relationship("Client", back_populates="dns_zones")
    domain = db.relationship("Domain", back_populates="dns_zone")
    records = db.relationship("DNSRecord", back_populates="zone", cascade="all, delete-orphan")


class DNSRecord(TimestampMixin, db.Model):
    __tablename__ = "dns_records"

    id = db.Column(db.Integer, primary_key=True)
    zone_id = db.Column(db.Integer, db.ForeignKey("dns_zones.id"), nullable=False, index=True)
    name = db.Column(db.String(255), nullable=False)
    type = db.Column(db.String(16), nullable=False, index=True)
    value = db.Column(db.String(255), nullable=False)
    priority = db.Column(db.Integer, nullable=True)
    ttl = db.Column(db.Integer, nullable=False, default=3600)
    disabled = db.Column(db.Boolean, nullable=False, default=False)

    zone = db.relationship("DNSZone", back_populates="records")

    __table_args__ = (
        UniqueConstraint("zone_id", "name", "type", "value", name="uq_dns_record_unique"),
    )


class SSLCertificate(TimestampMixin, db.Model):
    __tablename__ = "ssl_certificates"

    id = db.Column(db.Integer, primary_key=True)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), unique=True, nullable=True, index=True)
    subdomain_id = db.Column(db.Integer, db.ForeignKey("subdomains.id"), unique=True, nullable=True, index=True)
    provider = db.Column(db.String(64), nullable=False, default="letsencrypt")
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    common_name = db.Column(db.String(255), nullable=False, unique=True, index=True)
    valid_from = db.Column(db.DateTime, nullable=True)
    valid_until = db.Column(db.DateTime, nullable=True, index=True)
    auto_renew = db.Column(db.Boolean, nullable=False, default=True)
    certificate_path = db.Column(db.String(255), nullable=True)
    private_key_path = db.Column(db.String(255), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    domain = db.relationship("Domain", back_populates="ssl_certificate")
    subdomain = db.relationship("Subdomain", back_populates="ssl_certificate")

    @property
    def target_name(self) -> str:
        return self.common_name

    @property
    def client_id(self) -> int | None:
        if self.domain is not None:
            return self.domain.client_id
        if self.subdomain is not None:
            return self.subdomain.domain.client_id
        return None


class DomainRegistration(TimestampMixin, db.Model):
    __tablename__ = "domain_registrations"

    id = db.Column(db.Integer, primary_key=True)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), unique=True, nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    registrar = db.Column(db.String(64), nullable=False, default="mock", index=True)
    external_registration_id = db.Column(db.String(191), nullable=True, unique=True, index=True)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    registered_on = db.Column(db.Date, nullable=True)
    expires_on = db.Column(db.Date, nullable=True, index=True)
    auto_renew = db.Column(db.Boolean, nullable=False, default=True)
    name_servers_json = db.Column(db.JSON, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    last_synced_at = db.Column(db.DateTime, nullable=True, index=True)
    last_sync_status = db.Column(db.String(32), nullable=True, index=True)
    last_sync_message = db.Column(db.String(255), nullable=True)

    domain = db.relationship("Domain", back_populates="registration")
    client = db.relationship("Client", back_populates="domain_registrations")


class Mailbox(TimestampMixin, db.Model):
    __tablename__ = "mailboxes"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), nullable=False, index=True)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    quota_mb = db.Column(db.Integer, nullable=False, default=1024)
    status = db.Column(db.String(32), nullable=False, default="active", index=True)

    client = db.relationship("Client", back_populates="mailboxes")
    domain = db.relationship("Domain")
    aliases = db.relationship("MailAlias", back_populates="mailbox", cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")


class MailAlias(TimestampMixin, db.Model):
    __tablename__ = "mail_aliases"

    id = db.Column(db.Integer, primary_key=True)
    mailbox_id = db.Column(db.Integer, db.ForeignKey("mailboxes.id"), nullable=False, index=True)
    source = db.Column(db.String(255), nullable=False, unique=True)
    destination = db.Column(db.String(255), nullable=False)
    alias_type = db.Column(db.String(32), nullable=False, default="alias")

    mailbox = db.relationship("Mailbox", back_populates="aliases")


class Backup(TimestampMixin, db.Model):
    __tablename__ = "backups"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    domain_id = db.Column(db.Integer, db.ForeignKey("domains.id"), nullable=True, index=True)
    database_id = db.Column(db.Integer, db.ForeignKey("databases.id"), nullable=True, index=True)
    backup_type = db.Column(db.String(32), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    storage_path = db.Column(db.String(255), nullable=False)
    storage_target_id = db.Column(db.Integer, db.ForeignKey("external_backup_targets.id"), nullable=True, index=True)
    external_location = db.Column(db.String(1024), nullable=True)
    size_bytes = db.Column(db.BigInteger, nullable=True)
    scheduled_for = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    retention_until = db.Column(db.DateTime, nullable=True)
    last_verified_at = db.Column(db.DateTime, nullable=True)
    last_verification_status = db.Column(db.String(32), nullable=True, index=True)
    last_verification_message = db.Column(db.String(500), nullable=True)

    client = db.relationship("Client", back_populates="backups")
    domain = db.relationship("Domain")
    database = db.relationship("HostingDatabase")
    storage_target = db.relationship("ExternalBackupTarget", back_populates="backups")
    verification_runs = db.relationship("BackupVerificationRun", back_populates="backup", cascade="all, delete-orphan")


class ClientSSHKey(TimestampMixin, db.Model):
    __tablename__ = "client_ssh_keys"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    label = db.Column(db.String(120), nullable=False)
    key_type = db.Column(db.String(32), nullable=False, index=True)
    public_key = db.Column(db.Text, nullable=False)
    fingerprint_sha256 = db.Column(db.String(128), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, default="active", index=True)
    last_installed_at = db.Column(db.DateTime, nullable=True)
    last_used_at = db.Column(db.DateTime, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="ssh_keys")
    created_by = db.relationship("User", back_populates="created_ssh_keys", foreign_keys=[created_by_user_id])

    __table_args__ = (
        UniqueConstraint("client_id", "fingerprint_sha256", name="uq_client_ssh_key_client_fingerprint"),
    )


class Ticket(TimestampMixin, db.Model):
    __tablename__ = "tickets"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    assigned_to_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    subject = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(64), nullable=True, index=True)
    priority = db.Column(db.String(16), nullable=False, default="normal", index=True)
    status = db.Column(db.String(32), nullable=False, default="open", index=True)
    last_message_at = db.Column(db.DateTime, nullable=True, index=True)
    first_response_at = db.Column(db.DateTime, nullable=True, index=True)
    first_response_due_at = db.Column(db.DateTime, nullable=True, index=True)
    escalated_at = db.Column(db.DateTime, nullable=True, index=True)
    closed_at = db.Column(db.DateTime, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="tickets")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id], back_populates="created_tickets")
    assigned_to = db.relationship("User", foreign_keys=[assigned_to_user_id], back_populates="assigned_tickets")
    messages = db.relationship(
        "TicketMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMessage.created_at.asc()",
    )
    attachments = db.relationship(
        "TicketAttachment",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketAttachment.created_at.asc()",
    )
    macro_usages = db.relationship(
        "TicketMacroUsage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMacroUsage.created_at.asc()",
    )

    @property
    def display_number(self) -> str:
        if self.id is None:
            return "TKT-NEW"
        return f"TKT-{self.id:06d}"


class TicketMessage(TimestampMixin, db.Model):
    __tablename__ = "ticket_messages"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    author_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    is_internal = db.Column(db.Boolean, nullable=False, default=False, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    ticket = db.relationship("Ticket", back_populates="messages")
    author = db.relationship("User", back_populates="ticket_messages", foreign_keys=[author_user_id])
    attachments = db.relationship("TicketAttachment", back_populates="ticket_message", cascade="all, delete-orphan")
    macro_usages = db.relationship("TicketMacroUsage", back_populates="ticket_message", cascade="all, delete-orphan")


class TicketAttachment(TimestampMixin, db.Model):
    __tablename__ = "ticket_attachments"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    ticket_message_id = db.Column(db.Integer, db.ForeignKey("ticket_messages.id"), nullable=True, index=True)
    uploaded_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    storage_path = db.Column(db.String(1024), nullable=False)
    mime_type = db.Column(db.String(120), nullable=True)
    size_bytes = db.Column(db.BigInteger, nullable=False, default=0)

    ticket = db.relationship("Ticket", back_populates="attachments")
    ticket_message = db.relationship("TicketMessage", back_populates="attachments")
    uploaded_by = db.relationship("User")


class TicketMacro(TimestampMixin, db.Model):
    __tablename__ = "ticket_macros"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    category = db.Column(db.String(32), nullable=False, index=True)
    visibility_scope = db.Column(db.String(32), nullable=False, default="all_staff", index=True)
    subject_template = db.Column(db.String(200), nullable=True)
    body_template = db.Column(db.Text, nullable=False)
    placeholders_json = db.Column(db.JSON, nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    created_by = db.relationship("User", back_populates="ticket_macro_creations", foreign_keys=[created_by_user_id])
    updated_by = db.relationship("User", back_populates="ticket_macro_updates", foreign_keys=[updated_by_user_id])
    usages = db.relationship("TicketMacroUsage", back_populates="macro", cascade="all, delete-orphan")


class TicketMacroUsage(TimestampMixin, db.Model):
    __tablename__ = "ticket_macro_usages"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    ticket_message_id = db.Column(db.Integer, db.ForeignKey("ticket_messages.id"), nullable=True, index=True)
    macro_id = db.Column(db.Integer, db.ForeignKey("ticket_macros.id"), nullable=False, index=True)
    used_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    rendered_body = db.Column(db.Text, nullable=True)
    render_error = db.Column(db.String(255), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    ticket = db.relationship("Ticket", back_populates="macro_usages")
    ticket_message = db.relationship("TicketMessage", back_populates="macro_usages")
    macro = db.relationship("TicketMacro", back_populates="usages")
    used_by = db.relationship("User", back_populates="ticket_macro_usages", foreign_keys=[used_by_user_id])


class ClientResourceSample(TimestampMixin, db.Model):
    __tablename__ = "client_resource_samples"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    cpu_percent = db.Column(db.Numeric(7, 2), nullable=True)
    memory_mb = db.Column(db.Numeric(12, 2), nullable=True)
    memory_limit_mb = db.Column(db.Numeric(12, 2), nullable=True)
    disk_mb = db.Column(db.Numeric(12, 2), nullable=True)
    inode_count = db.Column(db.BigInteger, nullable=True)
    database_count = db.Column(db.Integer, nullable=True)
    mailbox_count = db.Column(db.Integer, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="resource_samples")


class BackupRestoreJob(TimestampMixin, db.Model):
    __tablename__ = "backup_restore_jobs"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    backup_id = db.Column(db.Integer, db.ForeignKey("backups.id"), nullable=False, index=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    restore_type = db.Column(db.String(32), nullable=False, default="files")
    target_path = db.Column(db.String(1024), nullable=True)
    message = db.Column(db.String(500), nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="restore_jobs")
    backup = db.relationship("Backup")
    requested_by = db.relationship("User")


class ApiToken(TimestampMixin, db.Model):
    __tablename__ = "api_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    token_prefix = db.Column(db.String(24), nullable=False, index=True)
    token_hash = db.Column(db.String(128), nullable=False)
    scopes_json = db.Column(db.JSON, nullable=False, default=list)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True, index=True)

    user = db.relationship("User", back_populates="api_tokens")
    idempotency_keys = db.relationship("ApiIdempotencyKey", back_populates="api_token", cascade="all, delete-orphan")


class WebhookEndpoint(TimestampMixin, db.Model):
    __tablename__ = "webhook_endpoints"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False)
    target_url = db.Column(db.String(500), nullable=False)
    secret = db.Column(db.String(255), nullable=True)
    event_types_json = db.Column(db.JSON, nullable=False, default=list)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    last_error = db.Column(db.String(500), nullable=True)
    last_success_at = db.Column(db.DateTime, nullable=True)

    client = db.relationship("Client", back_populates="webhook_endpoints")
    created_by = db.relationship("User")
    deliveries = db.relationship("WebhookDelivery", back_populates="endpoint", cascade="all, delete-orphan")


class WebhookDelivery(TimestampMixin, db.Model):
    __tablename__ = "webhook_deliveries"

    id = db.Column(db.Integer, primary_key=True)
    endpoint_id = db.Column(db.Integer, db.ForeignKey("webhook_endpoints.id"), nullable=False, index=True)
    event_type = db.Column(db.String(120), nullable=False, index=True)
    payload_json = db.Column(db.JSON, nullable=True)
    status_code = db.Column(db.Integer, nullable=True)
    success = db.Column(db.Boolean, nullable=False, default=False, index=True)
    response_excerpt = db.Column(db.String(500), nullable=True)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    max_attempts = db.Column(db.Integer, nullable=False, default=5)
    next_retry_at = db.Column(db.DateTime, nullable=True, index=True)
    dead_lettered = db.Column(db.Boolean, nullable=False, default=False, index=True)
    dead_lettered_at = db.Column(db.DateTime, nullable=True)
    dead_letter_reason = db.Column(db.String(500), nullable=True)
    idempotency_key = db.Column(db.String(191), nullable=True, index=True)
    destination_url = db.Column(db.String(500), nullable=True)
    request_headers_json = db.Column(db.JSON, nullable=True)
    request_body_sha256 = db.Column(db.String(64), nullable=True)
    attempted_at = db.Column(db.DateTime, nullable=True, index=True)

    endpoint = db.relationship("WebhookEndpoint", back_populates="deliveries")


class ResourceLimitAlert(TimestampMixin, db.Model):
    __tablename__ = "resource_limit_alerts"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    resource_key = db.Column(db.String(32), nullable=False, index=True)
    threshold_label = db.Column(db.String(32), nullable=False, index=True)
    threshold_percent = db.Column(db.Integer, nullable=True)
    usage_value = db.Column(db.Numeric(14, 2), nullable=True)
    limit_value = db.Column(db.Numeric(14, 2), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="active", index=True)
    message = db.Column(db.String(255), nullable=False)
    triggered_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True, index=True)
    last_measured_at = db.Column(db.DateTime, nullable=True)
    notification_channels_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="resource_alerts")


class ExternalBackupTarget(TimestampMixin, db.Model):
    __tablename__ = "external_backup_targets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True, index=True)
    provider = db.Column(db.String(32), nullable=False, index=True)
    endpoint_url = db.Column(db.String(500), nullable=True)
    bucket_name = db.Column(db.String(255), nullable=False)
    region = db.Column(db.String(64), nullable=True)
    access_key_env = db.Column(db.String(120), nullable=False)
    secret_key_env = db.Column(db.String(120), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=False, index=True)
    last_checked_at = db.Column(db.DateTime, nullable=True)
    last_check_status = db.Column(db.String(32), nullable=True)
    last_check_message = db.Column(db.String(500), nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    created_by = db.relationship("User")
    backups = db.relationship("Backup", back_populates="storage_target")
    service_plans = db.relationship("ServicePlan", back_populates="backup_storage_target")


class BackupVerificationRun(TimestampMixin, db.Model):
    __tablename__ = "backup_verification_runs"

    id = db.Column(db.Integer, primary_key=True)
    backup_id = db.Column(db.Integer, db.ForeignKey("backups.id"), nullable=False, index=True)
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    schedule_type = db.Column(db.String(16), nullable=False, default="daily")
    verified_at = db.Column(db.DateTime, nullable=True)
    restore_duration_ms = db.Column(db.Integer, nullable=True)
    validation_message = db.Column(db.String(500), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    backup = db.relationship("Backup", back_populates="verification_runs")


class ApiIdempotencyKey(TimestampMixin, db.Model):
    __tablename__ = "api_idempotency_keys"

    id = db.Column(db.Integer, primary_key=True)
    api_token_id = db.Column(db.Integer, db.ForeignKey("api_tokens.id"), nullable=False, index=True)
    idempotency_key = db.Column(db.String(128), nullable=False)
    method = db.Column(db.String(16), nullable=False)
    path = db.Column(db.String(255), nullable=False)
    request_hash = db.Column(db.String(64), nullable=False)
    response_status = db.Column(db.Integer, nullable=False)
    response_body_json = db.Column(db.JSON, nullable=True)
    processed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    api_token = db.relationship("ApiToken", back_populates="idempotency_keys")

    __table_args__ = (
        UniqueConstraint("api_token_id", "idempotency_key", "method", "path", name="uq_api_idempotency_token_key"),
    )


class UserSession(TimestampMixin, db.Model):
    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    session_token_hash = db.Column(db.String(128), nullable=False, unique=True, index=True)
    ip_address = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(500), nullable=True)
    last_activity_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    revoked_at = db.Column(db.DateTime, nullable=True, index=True)

    user = db.relationship("User", back_populates="sessions")


class RegistrationFraudCheck(TimestampMixin, db.Model):
    __tablename__ = "registration_fraud_checks"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    reviewed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    username = db.Column(db.String(80), nullable=False, index=True)
    ip_address = db.Column(db.String(45), nullable=True, index=True)
    user_agent = db.Column(db.String(500), nullable=True)
    score = db.Column(db.Integer, nullable=False, default=0, index=True)
    risk_level = db.Column(db.String(16), nullable=False, default="low", index=True)
    blocked = db.Column(db.Boolean, nullable=False, default=False, index=True)
    reasons_json = db.Column(db.JSON, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True, index=True)
    review_note = db.Column(db.String(255), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    user = db.relationship("User", foreign_keys=[user_id], back_populates="fraud_checks")
    reviewed_by = db.relationship("User", foreign_keys=[reviewed_by_user_id], back_populates="fraud_reviews")


class TwoFactorBackupCode(TimestampMixin, db.Model):
    __tablename__ = "two_factor_backup_codes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    code_hash = db.Column(db.String(128), nullable=False, index=True)
    used_at = db.Column(db.DateTime, nullable=True, index=True)

    user = db.relationship("User", back_populates="two_factor_backup_codes")


class OperatorPermission(TimestampMixin, db.Model):
    __tablename__ = "operator_permissions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    domain = db.Column(db.String(32), nullable=False)
    can_read = db.Column(db.Boolean, nullable=False, default=True)
    can_write = db.Column(db.Boolean, nullable=False, default=True)

    user = db.relationship("User", back_populates="operator_permissions")

    __table_args__ = (UniqueConstraint("user_id", "domain", name="uq_operator_permission_user_domain"),)


class StatusEvent(TimestampMixin, db.Model):
    __tablename__ = "status_events"

    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(16), nullable=False, index=True)
    state = db.Column(db.String(32), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    public_message = db.Column(db.Text, nullable=False)
    internal_note = db.Column(db.Text, nullable=True)
    affected_components_json = db.Column(db.JSON, nullable=True)
    starts_at = db.Column(db.DateTime, nullable=False, index=True)
    ends_at = db.Column(db.DateTime, nullable=True, index=True)
    resolved_at = db.Column(db.DateTime, nullable=True, index=True)
    is_public = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    created_by = db.relationship("User")


class MigrationJob(TimestampMixin, db.Model):
    __tablename__ = "migration_jobs"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    source_provider = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="draft", index=True)
    current_step = db.Column(db.String(32), nullable=False, default="preflight")
    progress_percent = db.Column(db.Integer, nullable=False, default=0)
    payload_encrypted = db.Column(db.Text, nullable=True)
    masked_summary = db.Column(db.String(255), nullable=True)
    last_error = db.Column(db.String(500), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    client = db.relationship("Client", back_populates="migration_jobs")
    requested_by = db.relationship("User")


class AutomationRule(TimestampMixin, db.Model):
    __tablename__ = "automation_rules"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, unique=True, index=True)
    description = db.Column(db.String(255), nullable=True)
    trigger_event = db.Column(db.String(120), nullable=False, index=True)
    conditions_json = db.Column(db.JSON, nullable=True)
    actions_json = db.Column(db.JSON, nullable=False, default=list)
    stop_on_match = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)

    executions = db.relationship("AutomationExecution", back_populates="rule", cascade="all, delete-orphan")


class AutomationExecution(TimestampMixin, db.Model):
    __tablename__ = "automation_executions"

    id = db.Column(db.Integer, primary_key=True)
    rule_id = db.Column(db.Integer, db.ForeignKey("automation_rules.id"), nullable=False, index=True)
    trigger_event = db.Column(db.String(120), nullable=False, index=True)
    event_fingerprint = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    message = db.Column(db.String(500), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    executed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    rule = db.relationship("AutomationRule", back_populates="executions")

    __table_args__ = (UniqueConstraint("rule_id", "event_fingerprint", name="uq_automation_rule_fingerprint"),)


class ApprovalRequest(TimestampMixin, db.Model):
    __tablename__ = "approval_requests"

    id = db.Column(db.Integer, primary_key=True)
    action_key = db.Column(db.String(64), nullable=False, index=True)
    target_type = db.Column(db.String(64), nullable=False, index=True)
    target_id = db.Column(db.String(120), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    executed_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default="pending", index=True)
    required_approvals = db.Column(db.Integer, nullable=False, default=1)
    min_approver_role = db.Column(db.String(32), nullable=False, default="operator")
    reason = db.Column(db.String(255), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    approved_at = db.Column(db.DateTime, nullable=True, index=True)
    rejected_at = db.Column(db.DateTime, nullable=True, index=True)
    executed_at = db.Column(db.DateTime, nullable=True, index=True)
    cancelled_at = db.Column(db.DateTime, nullable=True, index=True)
    execution_error = db.Column(db.String(500), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="approval_requests")
    requested_by = db.relationship(
        "User",
        back_populates="requested_approvals",
        foreign_keys=[requested_by_user_id],
    )
    executed_by = db.relationship(
        "User",
        back_populates="executed_approvals",
        foreign_keys=[executed_by_user_id],
    )
    decisions = db.relationship(
        "ApprovalDecision",
        back_populates="approval_request",
        cascade="all, delete-orphan",
        order_by="ApprovalDecision.created_at.asc()",
    )


class ApprovalDecision(TimestampMixin, db.Model):
    __tablename__ = "approval_decisions"

    id = db.Column(db.Integer, primary_key=True)
    approval_request_id = db.Column(db.Integer, db.ForeignKey("approval_requests.id"), nullable=False, index=True)
    decided_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    decision = db.Column(db.String(16), nullable=False, index=True)
    note = db.Column(db.String(255), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    approval_request = db.relationship("ApprovalRequest", back_populates="decisions")
    decided_by = db.relationship("User", back_populates="approval_decisions", foreign_keys=[decided_by_user_id])

    __table_args__ = (
        UniqueConstraint(
            "approval_request_id",
            "decided_by_user_id",
            name="uq_approval_decision_request_user",
        ),
    )


class ActivityLog(TimestampMixin, db.Model):
    __tablename__ = "activity_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    action = db.Column(db.String(120), nullable=False, index=True)
    entity_type = db.Column(db.String(120), nullable=False, index=True)
    entity_id = db.Column(db.String(120), nullable=True)
    description = db.Column(db.String(255), nullable=False)
    ip_address = db.Column(db.String(45), nullable=True)
    success = db.Column(db.Boolean, nullable=False, default=True, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    chain_sequence = db.Column(db.Integer, nullable=True)
    chain_prev_hash = db.Column(db.String(64), nullable=True)
    chain_hash = db.Column(db.String(64), nullable=True, index=True)
    chain_version = db.Column(db.String(16), nullable=True)
    chain_legacy = db.Column(db.Boolean, nullable=False, default=False, index=True)

    actor = db.relationship("User", back_populates="activity_logs")
    client = db.relationship("Client")


class HostsFileBackup(TimestampMixin, db.Model):
    __tablename__ = "hosts_file_backups"

    id = db.Column(db.Integer, primary_key=True)
    backup_name = db.Column(db.String(255), nullable=False, unique=True)
    backup_path = db.Column(db.String(255), nullable=False)
    checksum = db.Column(db.String(128), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    notes = db.Column(db.String(255), nullable=True)

    created_by = db.relationship("User")
    changes = db.relationship("HostsFileChange", back_populates="backup")


class HostsFileChange(TimestampMixin, db.Model):
    __tablename__ = "hosts_file_changes"

    id = db.Column(db.Integer, primary_key=True)
    backup_id = db.Column(db.Integer, db.ForeignKey("hosts_file_backups.id"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    ip_address = db.Column(db.String(45), nullable=False)
    hostname = db.Column(db.String(255), nullable=False)
    previous_value = db.Column(db.String(255), nullable=True)
    new_value = db.Column(db.String(255), nullable=True)
    success = db.Column(db.Boolean, nullable=False, default=True, index=True)
    message = db.Column(db.String(255), nullable=False)

    backup = db.relationship("HostsFileBackup", back_populates="changes")
    user = db.relationship("User")


class SystemSetting(TimestampMixin, db.Model):
    __tablename__ = "system_settings"

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(120), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(255), nullable=True)


class BulkOperation(TimestampMixin, db.Model):
    __tablename__ = "bulk_operations"

    id = db.Column(db.Integer, primary_key=True)
    operation_type = db.Column(db.String(64), nullable=False, index=True)
    target_type = db.Column(db.String(32), nullable=False, index=True)
    initiated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    dry_run = db.Column(db.Boolean, nullable=False, default=False)
    status = db.Column(db.String(32), nullable=False, default="completed", index=True)
    requested_filters_json = db.Column(db.JSON, nullable=True)
    result_summary_json = db.Column(db.JSON, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    initiated_by = db.relationship("User", back_populates="bulk_operations", foreign_keys=[initiated_by_user_id])
    items = db.relationship("BulkOperationItem", back_populates="operation", cascade="all, delete-orphan")


class BulkOperationItem(TimestampMixin, db.Model):
    __tablename__ = "bulk_operation_items"

    id = db.Column(db.Integer, primary_key=True)
    bulk_operation_id = db.Column(db.Integer, db.ForeignKey("bulk_operations.id"), nullable=False, index=True)
    entity_type = db.Column(db.String(64), nullable=False)
    entity_id = db.Column(db.String(120), nullable=False)
    success = db.Column(db.Boolean, nullable=False, default=False, index=True)
    message = db.Column(db.String(500), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    operation = db.relationship("BulkOperation", back_populates="items")


class ExportJob(TimestampMixin, db.Model):
    __tablename__ = "export_jobs"

    id = db.Column(db.Integer, primary_key=True)
    dataset = db.Column(db.String(32), nullable=False, index=True)
    format = db.Column(db.String(16), nullable=False, index=True)
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    filters_json = db.Column(db.JSON, nullable=True)
    status = db.Column(db.String(32), nullable=False, default="completed", index=True)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    error_message = db.Column(db.String(500), nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    requested_by = db.relationship("User", back_populates="export_jobs", foreign_keys=[requested_by_user_id])


class TenantRetentionPolicy(TimestampMixin, db.Model):
    __tablename__ = "tenant_retention_policies"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    resource_type = db.Column(db.String(64), nullable=False, index=True)
    anonymize_after_days = db.Column(db.Integer, nullable=True)
    delete_after_days = db.Column(db.Integer, nullable=True)
    legal_hold_enabled = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    notes = db.Column(db.String(255), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="retention_policies")

    __table_args__ = (
        UniqueConstraint("client_id", "resource_type", name="uq_retention_policy_client_resource"),
    )


class DataLegalHold(TimestampMixin, db.Model):
    __tablename__ = "data_legal_holds"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    resource_type = db.Column(db.String(64), nullable=False, index=True)
    resource_id = db.Column(db.String(120), nullable=True, index=True)
    status = db.Column(db.String(16), nullable=False, default="active", index=True)
    reason = db.Column(db.String(255), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    starts_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    released_at = db.Column(db.DateTime, nullable=True, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="legal_holds")
    created_by = db.relationship("User", back_populates="created_legal_holds", foreign_keys=[created_by_user_id])


class RetentionCleanupRun(TimestampMixin, db.Model):
    __tablename__ = "retention_cleanup_runs"

    id = db.Column(db.Integer, primary_key=True)
    run_key = db.Column(db.String(120), nullable=True, unique=True, index=True)
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    summary_json = db.Column(db.JSON, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    triggered_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    triggered_by = db.relationship("User", back_populates="retention_cleanup_runs", foreign_keys=[triggered_by_user_id])


class VaultSecret(TimestampMixin, db.Model):
    __tablename__ = "vault_secrets"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    secret_type = db.Column(db.String(64), nullable=False, index=True)
    description = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="active", index=True)
    current_version = db.Column(db.Integer, nullable=False, default=0)
    rotation_interval_days = db.Column(db.Integer, nullable=True)
    last_rotated_at = db.Column(db.DateTime, nullable=True, index=True)
    next_rotation_due_at = db.Column(db.DateTime, nullable=True, index=True)
    last_revealed_at = db.Column(db.DateTime, nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    client = db.relationship("Client", back_populates="vault_secrets")
    created_by = db.relationship("User", back_populates="created_vault_secrets", foreign_keys=[created_by_user_id])
    updated_by = db.relationship("User", back_populates="updated_vault_secrets", foreign_keys=[updated_by_user_id])
    versions = db.relationship(
        "VaultSecretVersion",
        back_populates="secret",
        cascade="all, delete-orphan",
        order_by="desc(VaultSecretVersion.version)",
    )

    __table_args__ = (
        UniqueConstraint("client_id", "name", name="uq_vault_secret_client_name"),
    )


class VaultSecretVersion(TimestampMixin, db.Model):
    __tablename__ = "vault_secret_versions"

    id = db.Column(db.Integer, primary_key=True)
    secret_id = db.Column(db.Integer, db.ForeignKey("vault_secrets.id"), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    value_encrypted = db.Column(db.Text, nullable=False)
    value_fingerprint = db.Column(db.String(64), nullable=True, index=True)
    is_current = db.Column(db.Boolean, nullable=False, default=False, index=True)
    rotated_reason = db.Column(db.String(255), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    secret = db.relationship("VaultSecret", back_populates="versions")
    created_by = db.relationship("User", back_populates="vault_secret_versions", foreign_keys=[created_by_user_id])

    __table_args__ = (
        UniqueConstraint("secret_id", "version", name="uq_vault_secret_version"),
    )


class EventStreamEntry(TimestampMixin, db.Model):
    __tablename__ = "event_stream_entries"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    event_type = db.Column(db.String(120), nullable=False, index=True)
    category = db.Column(db.String(64), nullable=False, index=True)
    severity = db.Column(db.String(16), nullable=False, default="info", index=True)
    source = db.Column(db.String(64), nullable=True, index=True)
    message = db.Column(db.String(255), nullable=False)
    payload_json = db.Column(db.JSON, nullable=True)
    event_fingerprint = db.Column(db.String(64), nullable=True, index=True)
    event_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    client = db.relationship("Client", back_populates="event_entries")
    actor = db.relationship("User", back_populates="event_entries", foreign_keys=[actor_user_id])


class ComplianceRun(TimestampMixin, db.Model):
    __tablename__ = "compliance_runs"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    score = db.Column(db.Integer, nullable=True)
    summary_json = db.Column(db.JSON, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    triggered_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    client = db.relationship("Client", back_populates="compliance_runs")
    triggered_by = db.relationship("User", back_populates="compliance_runs", foreign_keys=[triggered_by_user_id])
    results = db.relationship("ComplianceResult", back_populates="run", cascade="all, delete-orphan")


class ComplianceResult(TimestampMixin, db.Model):
    __tablename__ = "compliance_results"

    id = db.Column(db.Integer, primary_key=True)
    run_id = db.Column(db.Integer, db.ForeignKey("compliance_runs.id"), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    check_code = db.Column(db.String(64), nullable=False, index=True)
    status = db.Column(db.String(16), nullable=False, index=True)
    severity = db.Column(db.String(16), nullable=False, default="medium")
    score = db.Column(db.Integer, nullable=True)
    message = db.Column(db.String(255), nullable=False)
    details_json = db.Column(db.JSON, nullable=True)
    evidence_ref = db.Column(db.String(255), nullable=True)

    run = db.relationship("ComplianceRun", back_populates="results")
    client = db.relationship("Client", back_populates="compliance_results")


class ComplianceChecklistItem(TimestampMixin, db.Model):
    __tablename__ = "compliance_checklist_items"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    control_code = db.Column(db.String(64), nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(32), nullable=False, default="not_started", index=True)
    owner_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    due_date = db.Column(db.Date, nullable=True, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    client = db.relationship("Client", back_populates="compliance_checklist_items")
    owner = db.relationship("User", back_populates="owned_compliance_controls", foreign_keys=[owner_user_id])
    created_by = db.relationship("User", back_populates="compliance_controls_created", foreign_keys=[created_by_user_id])
    updated_by = db.relationship("User", back_populates="compliance_controls_updated", foreign_keys=[updated_by_user_id])
    evidence_links = db.relationship("ComplianceEvidenceLink", back_populates="checklist_item", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("client_id", "control_code", name="uq_compliance_control_client_code"),
    )


class ComplianceEvidenceLink(TimestampMixin, db.Model):
    __tablename__ = "compliance_evidence_links"

    id = db.Column(db.Integer, primary_key=True)
    checklist_item_id = db.Column(db.Integer, db.ForeignKey("compliance_checklist_items.id"), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    evidence_type = db.Column(db.String(32), nullable=False, index=True)
    reference_id = db.Column(db.String(120), nullable=False, index=True)
    reference_label = db.Column(db.String(255), nullable=True)
    linked_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    checklist_item = db.relationship("ComplianceChecklistItem", back_populates="evidence_links")
    client = db.relationship("Client", back_populates="compliance_evidence_links")
    linked_by = db.relationship("User", back_populates="compliance_evidence_links", foreign_keys=[linked_by_user_id])

    __table_args__ = (
        UniqueConstraint("checklist_item_id", "evidence_type", "reference_id", name="uq_compliance_evidence_link"),
    )


class PolicyDocument(TimestampMixin, db.Model):
    __tablename__ = "policy_documents"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    scope = db.Column(db.String(32), nullable=False, default="global", index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    version = db.Column(db.String(32), nullable=False, default="v1")
    enforcement_mode = db.Column(db.String(16), nullable=False, default="advisory", index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    description = db.Column(db.String(255), nullable=True)
    definition_json = db.Column(db.JSON, nullable=False, default=dict)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    client = db.relationship("Client", back_populates="policy_documents")
    created_by = db.relationship("User", back_populates="policy_documents_created", foreign_keys=[created_by_user_id])
    updated_by = db.relationship("User", back_populates="policy_documents_updated", foreign_keys=[updated_by_user_id])
    evaluations = db.relationship("PolicyEvaluation", back_populates="policy", cascade="all, delete-orphan")


class PolicyEvaluation(TimestampMixin, db.Model):
    __tablename__ = "policy_evaluations"

    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey("policy_documents.id"), nullable=False, index=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    event_type = db.Column(db.String(120), nullable=False, index=True)
    target_type = db.Column(db.String(64), nullable=True, index=True)
    target_id = db.Column(db.String(120), nullable=True, index=True)
    decision = db.Column(db.String(16), nullable=False, index=True)
    message = db.Column(db.String(255), nullable=True)
    input_json = db.Column(db.JSON, nullable=True)
    evaluated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    policy = db.relationship("PolicyDocument", back_populates="evaluations")
    client = db.relationship("Client", back_populates="policy_evaluations")
    evaluated_by = db.relationship("User", back_populates="policy_evaluations", foreign_keys=[evaluated_by_user_id])


class ClientOnboardingState(TimestampMixin, db.Model):
    __tablename__ = "client_onboarding_states"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, unique=True, index=True)
    completed_steps_json = db.Column(db.JSON, nullable=False, default=list)
    skipped_steps_json = db.Column(db.JSON, nullable=False, default=list)
    last_step = db.Column(db.String(64), nullable=True)
    completion_percent = db.Column(db.Integer, nullable=False, default=0)
    completed_at = db.Column(db.DateTime, nullable=True, index=True)
    metadata_json = db.Column(db.JSON, nullable=True)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    client = db.relationship("Client", back_populates="onboarding_state")
    updated_by = db.relationship("User", back_populates="onboarding_state_updates", foreign_keys=[updated_by_user_id])


class DisasterRecoveryProfile(TimestampMixin, db.Model):
    __tablename__ = "disaster_recovery_profiles"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, unique=True, index=True)
    primary_region = db.Column(db.String(64), nullable=True)
    secondary_region = db.Column(db.String(64), nullable=True)
    rpo_target_minutes = db.Column(db.Integer, nullable=False, default=1440)
    rto_target_minutes = db.Column(db.Integer, nullable=False, default=240)
    failover_last_tested_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.String(255), nullable=True)
    metadata_json = db.Column(db.JSON, nullable=True)

    client = db.relationship("Client", back_populates="dr_profile")


class DisasterRecoveryCheckRun(TimestampMixin, db.Model):
    __tablename__ = "disaster_recovery_check_runs"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=True, index=True)
    status = db.Column(db.String(32), nullable=False, default="queued", index=True)
    score = db.Column(db.Integer, nullable=True)
    rpo_minutes = db.Column(db.Integer, nullable=True)
    rto_minutes = db.Column(db.Integer, nullable=True)
    message = db.Column(db.String(255), nullable=True)
    details_json = db.Column(db.JSON, nullable=True)
    checked_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    checked_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True, index=True)

    client = db.relationship("Client", back_populates="dr_check_runs")
    checked_by = db.relationship("User", back_populates="dr_checks", foreign_keys=[checked_by_user_id])


Index("ix_activity_logs_entity", ActivityLog.entity_type, ActivityLog.entity_id)
Index("ix_billing_cycles_due_status", BillingCycle.due_date, BillingCycle.status)
Index("ix_client_services_type_status", ClientService.service_type, ClientService.status)
Index("ix_hosts_changes_host_action", HostsFileChange.hostname, HostsFileChange.action)
Index("ix_online_payments_client_status", OnlinePayment.client_id, OnlinePayment.status)
Index("ix_tickets_client_status", Ticket.client_id, Ticket.status)
Index("ix_tickets_status_priority", Ticket.status, Ticket.priority)
Index("ix_ticket_messages_ticket_created", TicketMessage.ticket_id, TicketMessage.created_at)
Index("ix_ticket_attachments_ticket_created", TicketAttachment.ticket_id, TicketAttachment.created_at)
Index("ix_ticket_macros_category_active", TicketMacro.category, TicketMacro.is_active)
Index("ix_ticket_macro_usages_ticket_created", TicketMacroUsage.ticket_id, TicketMacroUsage.created_at)
Index("ix_resource_samples_client_created", ClientResourceSample.client_id, ClientResourceSample.created_at)
Index("ix_restore_jobs_client_status", BackupRestoreJob.client_id, BackupRestoreJob.status)
Index("ix_api_tokens_user_revoked", ApiToken.user_id, ApiToken.revoked_at)
Index("ix_webhooks_active_client", WebhookEndpoint.is_active, WebhookEndpoint.client_id)
Index("ix_resource_alert_client_state", ResourceLimitAlert.client_id, ResourceLimitAlert.status)
Index("ix_webhook_delivery_retry", WebhookDelivery.next_retry_at, WebhookDelivery.dead_lettered)
Index("ix_status_events_public_state", StatusEvent.is_public, StatusEvent.state)
Index("ix_migration_jobs_client_status", MigrationJob.client_id, MigrationJob.status)
Index("ix_automation_exec_rule_status", AutomationExecution.rule_id, AutomationExecution.status)
Index("ix_approval_requests_action_status", ApprovalRequest.action_key, ApprovalRequest.status)
Index("ix_approval_requests_target_status", ApprovalRequest.target_type, ApprovalRequest.target_id, ApprovalRequest.status)
Index("ix_approval_decisions_request_decision", ApprovalDecision.approval_request_id, ApprovalDecision.decision)
Index("ix_activity_logs_chain_integrity", ActivityLog.chain_sequence, ActivityLog.chain_hash)
Index("ix_client_ssh_keys_client_status", ClientSSHKey.client_id, ClientSSHKey.status)
Index("ix_bulk_operations_type_status", BulkOperation.operation_type, BulkOperation.status)
Index("ix_export_jobs_dataset_created", ExportJob.dataset, ExportJob.created_at)
Index("ix_domain_registrations_provider_expiry", DomainRegistration.registrar, DomainRegistration.expires_on)
Index("ix_overdue_reminders_client_sent", OverdueReminder.client_id, OverdueReminder.sent_at)
Index("ix_fraud_checks_level_created", RegistrationFraudCheck.risk_level, RegistrationFraudCheck.created_at)
Index("ix_retention_policy_client_resource", TenantRetentionPolicy.client_id, TenantRetentionPolicy.resource_type)
Index("ix_legal_holds_scope_status", DataLegalHold.client_id, DataLegalHold.resource_type, DataLegalHold.status)
Index("ix_retention_runs_status_started", RetentionCleanupRun.status, RetentionCleanupRun.started_at)
Index("ix_vault_secret_rotation_due", VaultSecret.next_rotation_due_at, VaultSecret.status)
Index("ix_vault_secret_version_current", VaultSecretVersion.secret_id, VaultSecretVersion.is_current)
Index("ix_event_stream_category_time", EventStreamEntry.category, EventStreamEntry.event_at)
Index("ix_event_stream_client_time", EventStreamEntry.client_id, EventStreamEntry.event_at)
Index("ix_compliance_result_run_status", ComplianceResult.run_id, ComplianceResult.status)
Index("ix_compliance_control_client_status", ComplianceChecklistItem.client_id, ComplianceChecklistItem.status)
Index("ix_compliance_evidence_type_ref", ComplianceEvidenceLink.evidence_type, ComplianceEvidenceLink.reference_id)
Index("ix_policy_doc_scope_active", PolicyDocument.scope, PolicyDocument.is_active)
Index("ix_policy_eval_event_decision", PolicyEvaluation.event_type, PolicyEvaluation.decision)
Index("ix_onboarding_client_percent", ClientOnboardingState.client_id, ClientOnboardingState.completion_percent)
Index("ix_dr_checks_client_time", DisasterRecoveryCheckRun.client_id, DisasterRecoveryCheckRun.checked_at)
