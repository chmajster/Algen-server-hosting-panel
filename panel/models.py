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


class ServicePlan(TimestampMixin, db.Model):
    __tablename__ = "service_plans"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False, index=True)
    code = db.Column(db.String(80), unique=True, nullable=False, index=True)
    description = db.Column(db.Text, nullable=True)
    monthly_price = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    daily_price = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    yearly_price = db.Column(db.Numeric(12, 2), nullable=False, default=Decimal("0.00"))
    limits_json = db.Column(db.JSON, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    services = db.relationship("ClientService", back_populates="plan")


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
    size_bytes = db.Column(db.BigInteger, nullable=True)
    scheduled_for = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    client = db.relationship("Client", back_populates="backups")
    domain = db.relationship("Domain")
    database = db.relationship("HostingDatabase")


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


class ClientResourceSample(TimestampMixin, db.Model):
    __tablename__ = "client_resource_samples"

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey("clients.id"), nullable=False, index=True)
    cpu_percent = db.Column(db.Numeric(7, 2), nullable=True)
    memory_mb = db.Column(db.Numeric(12, 2), nullable=True)
    memory_limit_mb = db.Column(db.Numeric(12, 2), nullable=True)
    disk_mb = db.Column(db.Numeric(12, 2), nullable=True)
    inode_count = db.Column(db.BigInteger, nullable=True)
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
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True, index=True)

    user = db.relationship("User", back_populates="api_tokens")


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
    attempted_at = db.Column(db.DateTime, nullable=True, index=True)

    endpoint = db.relationship("WebhookEndpoint", back_populates="deliveries")


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


Index("ix_activity_logs_entity", ActivityLog.entity_type, ActivityLog.entity_id)
Index("ix_billing_cycles_due_status", BillingCycle.due_date, BillingCycle.status)
Index("ix_client_services_type_status", ClientService.service_type, ClientService.status)
Index("ix_hosts_changes_host_action", HostsFileChange.hostname, HostsFileChange.action)
Index("ix_online_payments_client_status", OnlinePayment.client_id, OnlinePayment.status)
Index("ix_tickets_client_status", Ticket.client_id, Ticket.status)
Index("ix_tickets_status_priority", Ticket.status, Ticket.priority)
Index("ix_ticket_messages_ticket_created", TicketMessage.ticket_id, TicketMessage.created_at)
Index("ix_ticket_attachments_ticket_created", TicketAttachment.ticket_id, TicketAttachment.created_at)
Index("ix_resource_samples_client_created", ClientResourceSample.client_id, ClientResourceSample.created_at)
Index("ix_restore_jobs_client_status", BackupRestoreJob.client_id, BackupRestoreJob.status)
Index("ix_api_tokens_user_revoked", ApiToken.user_id, ApiToken.revoked_at)
Index("ix_webhooks_active_client", WebhookEndpoint.is_active, WebhookEndpoint.client_id)
