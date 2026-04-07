from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL",
        "sqlite:///" + str(BASE_DIR / "storage" / "dev.db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    REMEMBER_COOKIE_HTTPONLY = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    APP_NAME = os.getenv("APP_NAME", "Hosting Panel")
    APP_ENV = os.getenv("APP_ENV", "development")
    APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
    APP_PORT = int(os.getenv("APP_PORT", "5000"))
    PREFERRED_URL_SCHEME = os.getenv("PREFERRED_URL_SCHEME", "http")
    STORAGE_ROOT = os.getenv("STORAGE_ROOT", str(BASE_DIR / "storage" / "uploads"))
    CLIENT_HOME_ROOT = os.getenv("CLIENT_HOME_ROOT", str(BASE_DIR / "storage" / "clients"))
    BACKUP_ROOT = os.getenv("BACKUP_ROOT", str(BASE_DIR / "storage" / "backups"))
    HOSTS_HELPER_PATH = os.getenv(
        "HOSTS_HELPER_PATH",
        "/usr/local/bin/hosting-panel-hosts-helper",
    )
    HOSTS_BACKUP_DIR = os.getenv(
        "HOSTS_BACKUP_DIR",
        "/var/backups/hosting-panel/hosts",
    )
    HOSTS_SUDO_BIN = os.getenv("HOSTS_SUDO_BIN", "/usr/bin/sudo")
    HOSTS_ALLOWED_FILE = os.getenv("HOSTS_ALLOWED_FILE", "/etc/hosts")
    SSL_HELPER_PATH = os.getenv("SSL_HELPER_PATH", "/usr/local/bin/hosting-panel-ssl-helper")
    LETSENCRYPT_EMAIL = os.getenv("LETSENCRYPT_EMAIL", "admin@example.com")
    DEFAULT_TIMEZONE = os.getenv("DEFAULT_TIMEZONE", "UTC")
    RATELIMIT_DEFAULT = os.getenv("RATELIMIT_DEFAULT", "200/day;50/hour")
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    LOGIN_RATELIMIT = os.getenv("LOGIN_RATELIMIT", "10 per 10 minutes")
    SELF_REGISTRATION_ENABLED = os.getenv("SELF_REGISTRATION_ENABLED", "true").lower() == "true"
    REGISTRATION_AUTO_LOGIN = os.getenv("REGISTRATION_AUTO_LOGIN", "true").lower() == "true"
    TWO_FACTOR_AVAILABLE = os.getenv("TWO_FACTOR_AVAILABLE", "false").lower() == "true"
    TWO_FACTOR_ISSUER = os.getenv("TWO_FACTOR_ISSUER", APP_NAME)
    TWO_FACTOR_LOGIN_RATELIMIT = os.getenv("TWO_FACTOR_LOGIN_RATELIMIT", "10 per 10 minutes")
    TWO_FACTOR_EMAIL_ENABLED = os.getenv("TWO_FACTOR_EMAIL_ENABLED", "true").lower() == "true"
    TWO_FACTOR_EMAIL_CODE_TTL_SECONDS = int(os.getenv("TWO_FACTOR_EMAIL_CODE_TTL_SECONDS", "300"))
    TWO_FACTOR_EMAIL_SUBJECT = os.getenv("TWO_FACTOR_EMAIL_SUBJECT", "Kod logowania 2FA")
    MAIL_SERVER = os.getenv("MAIL_SERVER", "")
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").lower() == "true"
    MAIL_USE_SSL = os.getenv("MAIL_USE_SSL", "false").lower() == "true"
    MAIL_FROM = os.getenv("MAIL_FROM", "")
    TICKETS_EMAIL_NOTIFICATIONS_ENABLED = os.getenv("TICKETS_EMAIL_NOTIFICATIONS_ENABLED", "true").lower() == "true"
    TICKETS_EMAIL_SUBJECT_NEW_CLIENT_TICKET = os.getenv(
        "TICKETS_EMAIL_SUBJECT_NEW_CLIENT_TICKET",
        "Nowy ticket klienta: {ticket}",
    )
    TICKETS_EMAIL_SUBJECT_CLIENT_REPLY = os.getenv(
        "TICKETS_EMAIL_SUBJECT_CLIENT_REPLY",
        "Nowa odpowiedz klienta: {ticket}",
    )
    TICKETS_EMAIL_SUBJECT_STAFF_REPLY = os.getenv(
        "TICKETS_EMAIL_SUBJECT_STAFF_REPLY",
        "Nowa odpowiedz supportu: {ticket}",
    )
    BILLING_GRACE_DAYS = int(os.getenv("BILLING_GRACE_DAYS", "3"))
    BILLING_AUTO_RESUME = os.getenv("BILLING_AUTO_RESUME", "true").lower() == "true"
    ONLINE_PAYMENTS_ENABLED = os.getenv("ONLINE_PAYMENTS_ENABLED", "false").lower() == "true"
    ONLINE_PAYMENTS_PROVIDER = os.getenv("ONLINE_PAYMENTS_PROVIDER", "stripe")
    ONLINE_PAYMENTS_CURRENCY = os.getenv("ONLINE_PAYMENTS_CURRENCY", "PLN")
    ONLINE_PAYMENTS_MIN_AMOUNT = os.getenv("ONLINE_PAYMENTS_MIN_AMOUNT", "5.00")
    ONLINE_PAYMENTS_MAX_AMOUNT = os.getenv("ONLINE_PAYMENTS_MAX_AMOUNT", "50000.00")
    ONLINE_PAYMENTS_SUCCESS_URL = os.getenv("ONLINE_PAYMENTS_SUCCESS_URL", "")
    ONLINE_PAYMENTS_CANCEL_URL = os.getenv("ONLINE_PAYMENTS_CANCEL_URL", "")
    STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
    STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_WEBHOOK_TOLERANCE_SECONDS = int(os.getenv("STRIPE_WEBHOOK_TOLERANCE_SECONDS", "300"))
    HOSTS_PROTECT_CRITICAL = os.getenv("HOSTS_PROTECT_CRITICAL", "true").lower() == "true"
    MAIL_DEFAULT_QUOTA_MB = int(os.getenv("MAIL_DEFAULT_QUOTA_MB", "1024"))
    DEFAULT_PHP_VERSION = os.getenv("DEFAULT_PHP_VERSION", "8.3")
    PHPMYADMIN_URL = os.getenv("PHPMYADMIN_URL", "/phpmyadmin/")
    CLIENT_APACHE_ENABLED = os.getenv("CLIENT_APACHE_ENABLED", "false").lower() == "true"
    CLIENT_APACHE_IMAGE = os.getenv("CLIENT_APACHE_IMAGE", "httpd:2.4")
    CLIENT_APACHE_BIND_ADDRESS = os.getenv("CLIENT_APACHE_BIND_ADDRESS", "127.0.0.1")
    CLIENT_APACHE_HTTP_PORT_BASE = int(os.getenv("CLIENT_APACHE_HTTP_PORT_BASE", "18000"))
    CLIENT_APACHE_CONTAINER_PREFIX = os.getenv("CLIENT_APACHE_CONTAINER_PREFIX", "hosting-panel-client-apache")
    CLIENT_APACHE_REMOVE_EMPTY = os.getenv("CLIENT_APACHE_REMOVE_EMPTY", "true").lower() == "true"
    SMOKE_TEST_LOG_FILE = os.getenv("SMOKE_TEST_LOG_FILE", "/var/log/hosting-panel/smoke-test.log")
    SMOKE_TEST_API_TOKEN = os.getenv("SMOKE_TEST_API_TOKEN", "")
    SMOKE_TEST_API_ALLOWLIST = os.getenv(
        "SMOKE_TEST_API_ALLOWLIST",
        "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
    )
    SMOKE_TEST_API_RATELIMIT = os.getenv("SMOKE_TEST_API_RATELIMIT", "5 per minute")
    SMOKE_TEST_SCHEDULE_ENABLED = os.getenv("SMOKE_TEST_SCHEDULE_ENABLED", "true").lower() == "true"
    SMOKE_TEST_INTERVAL = os.getenv("SMOKE_TEST_INTERVAL", "*:0/15")
    ADMIN_LOCAL_ONLY = os.getenv("ADMIN_LOCAL_ONLY", "true").lower() == "true"
    ADMIN_ALLOWED_NETWORKS = os.getenv(
        "ADMIN_ALLOWED_NETWORKS",
        "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
    )
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    WTF_CSRF_TIME_LIMIT = None
    PROXY_FIX_ENABLED = os.getenv("PROXY_FIX_ENABLED", "true").lower() == "true"


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}
