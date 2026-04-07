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

    def set_password(self, password: str) -> None:
        self.password_hash = bcrypt.generate_password_hash(password).decode("utf-8")

    def check_password(self, password: str) -> bool:
        return bcrypt.check_password_hash(self.password_hash, password)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def has_role(self, role_name: str) -> bool:
        return bool(self.role and self.role.name == role_name)


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
