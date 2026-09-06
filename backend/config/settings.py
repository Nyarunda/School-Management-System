import os
from pathlib import Path
from urllib.parse import urlparse
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
PRODUCTION = os.getenv("DJANGO_ENV", "development").lower() == "production"
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-change-me")
DEBUG = not PRODUCTION
ALLOWED_HOSTS = [host.strip() for host in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if host.strip()]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "apps.tenancy",
    "apps.admissions",
    "apps.students",
    "apps.guardians",
    "apps.activity",
    "apps.academics",
    "apps.finance",
    "apps.notifications",
    "apps.attendance",
    "apps.assessments",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.tenancy.middleware.TenantResolutionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ]},
    }
]
WSGI_APPLICATION = "config.wsgi.application"

if os.getenv("DB_ENGINE", "sqlite").lower() == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "school_management"),
            "USER": os.getenv("POSTGRES_USER", "school_management"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "school_management_dev"),
            "HOST": os.getenv("POSTGRES_HOST", "postgres"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

AUTH_PASSWORD_VALIDATORS = []
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "tenancy.User"

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "config.exceptions.exception_handler",
}

# Dev-only Fernet key (generated once for this repo's development default).
# Production MUST override via the environment; changing this key makes
# every previously-encrypted value (M-Pesa credentials) undecryptable.
FIELD_ENCRYPTION_KEY = os.getenv("FIELD_ENCRYPTION_KEY", "tcgm_bXMcNCa925qDWCcoGJCg_UHxRh0N50KsbHtbic=")

# Externally-reachable base URL used to build webhook callback URLs
# (e.g. M-Pesa's CallBackURL) registered with third-party providers.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")

# Redis/Celery are transport for async operational work only -- PostgreSQL
# owns durable state. Nothing in the web request path calls .delay(); every
# consumer polls PostgreSQL for due work on a Beat schedule, so a Redis
# outage delays work instead of losing or blocking it.
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_TASK_IGNORE_RESULT = True  # nothing calls .get()/AsyncResult on any task; no result backend needed
CELERY_TASK_ALWAYS_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "true").lower() == "true"
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BEAT_SCHEDULE = {
    "finance-resweep-unmatched-incoming-payments": {
        "task": "apps.finance.tasks.resweep_unmatched_incoming_payments",
        "schedule": 3600.0,
    },
    "notifications-dispatch-pending": {
        "task": "apps.notifications.tasks.dispatch_pending_notifications",
        "schedule": 30.0,
    },
    "notifications-reap-stale": {
        "task": "apps.notifications.tasks.reap_stale_notifications",
        "schedule": 300.0,
    },
}

if PRODUCTION:
    required = ("DJANGO_SECRET_KEY", "FIELD_ENCRYPTION_KEY", "PUBLIC_BASE_URL", "DJANGO_ALLOWED_HOSTS")
    if any(not os.getenv(name) for name in required):
        raise ImproperlyConfigured("Production requires explicit secret, encryption key, public URL, and allowed hosts")
    if SECRET_KEY == "development-only-change-me" or len(SECRET_KEY) < 32:
        raise ImproperlyConfigured("Production requires a non-default secret key of at least 32 characters")
    if FIELD_ENCRYPTION_KEY == "tcgm_bXMcNCa925qDWCcoGJCg_UHxRh0N50KsbHtbic=":
        raise ImproperlyConfigured("Production cannot use the development field encryption key")
    parsed_public_url = urlparse(PUBLIC_BASE_URL)
    if parsed_public_url.scheme != "https" or not parsed_public_url.hostname or parsed_public_url.hostname in ("localhost", "127.0.0.1", "::1") or parsed_public_url.query or parsed_public_url.fragment or parsed_public_url.username:
        raise ImproperlyConfigured("Production PUBLIC_BASE_URL must be an external HTTPS URL")
    if "*" in ALLOWED_HOSTS or DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("Production requires explicit allowed hosts and PostgreSQL")
    from cryptography.fernet import Fernet
    try:
        Fernet(FIELD_ENCRYPTION_KEY)
    except (ValueError, TypeError):
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY must be a valid Fernet key") from None
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
