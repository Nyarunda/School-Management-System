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
    "rest_framework.authtoken",
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
    "apps.timetable",
    "apps.staff",
    "apps.leave",
    "apps.documents",
    "apps.reporting",
    "apps.platform",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.RequestIdMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
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

# JSON in production (machine-parseable for log aggregation), a plain
# human-readable line otherwise -- both carry the request_id filter so every
# record (including config.cache's existing Redis-outage logs) is
# request-correlated. django.request is routed here at ERROR so a genuinely
# unhandled exception -- silent beyond Django's bare default until now -- is
# actually visible.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"request_id": {"()": "config.logging_utils.RequestIdFilter"}},
    "formatters": {
        "json": {"()": "config.logging_utils.JsonFormatter"},
        "console": {"format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "json" if PRODUCTION else "console",
        },
    },
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}

if os.getenv("DB_ENGINE", "sqlite").lower() == "postgres":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "school_management"),
            "USER": os.getenv("POSTGRES_USER", "school_management"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", "school_management_dev"),
            "HOST": os.getenv("POSTGRES_HOST", "postgres"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
            # Reuses a connection across requests instead of opening/closing
            # one per request. This trades per-request overhead for a
            # standing connection-count budget: (replica count *
            # GUNICORN_WORKERS * GUNICORN_THREADS) + Celery workers + Beat +
            # admin/migration headroom must stay under Postgres's
            # max_connections. Not a concern at today's scale; if it ever
            # becomes one, the standard next step is a connection pooler
            # (e.g. PgBouncer) in front of Postgres, not a setting here.
            "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "60")),
            "CONN_HEALTH_CHECKS": True,
        }
    }
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}

# apps.tenancy.services.accept_invite (Milestone 22.4) is the first real
# caller of validate_password() -- this setting alone still doesn't
# intercept every User.set_password()/create_user() call path (e.g.
# apps.tenancy.services.invite_user's set_unusable_password() is
# deliberately unvalidated, since it never sets a usable password).
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "tenancy.User"

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "config.exceptions.exception_handler",
    # TokenAuthentication is what apps.tenancy.auth_api.LoginView/InviteAcceptView
    # actually issue (Milestone 22.4) -- SessionAuthentication stays too since
    # it's DRF's own default and nothing here depends on removing it.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    # Per-user/per-IP abuse protection -- NOT tenant-level noisy-neighbor
    # protection (a tenant with 300 active users gets ~300x the throughput
    # of a tenant with one; that's a real gap this milestone deliberately
    # doesn't close -- see the plan's non-goals). UserRateThrottle scopes by
    # authenticated user id; AnonRateThrottle is the safety net for any
    # future AllowAny view that doesn't get an explicit scope. The 3 M-Pesa
    # webhook views (apps/finance/mpesa_api.py) opt into the
    # "mpesa_callback" scope instead via ScopedRateThrottle.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": os.getenv("THROTTLE_RATE_USER", "1000/hour"),
        "anon": os.getenv("THROTTLE_RATE_ANON", "100/hour"),
        # A protective ceiling against a flood/DoS, not ordinary traffic
        # shaping -- callback_token authentication and the human-verification
        # workflow (MpesaCallbackLog/CallbackVerifyView) are the real security
        # boundary here. Deliberately generous and env-overridable: rejecting
        # a legitimate Safaricom callback with 429 is worse than under-throttling,
        # since a dropped callback risks a payment never getting ingested.
        "mpesa_callback": os.getenv("THROTTLE_RATE_MPESA_CALLBACK", "120/min"),
        # RC Area 2: LoginView/InviteAcceptView (apps.tenancy.auth_api) are
        # credential-verification endpoints -- the general anon rate
        # (100/hour) is far too generous a brute-force window for them.
        "login": os.getenv("THROTTLE_RATE_LOGIN", "5/min"),
    },
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
# acks_late means a task is only ack'd (removed from the queue) AFTER it
# finishes -- if a worker is killed mid-task, the message is redelivered to
# another worker rather than silently lost. Safe today because every
# existing task is either durable_work-lease-protected (reporting,
# notifications) or independently idempotent under at-least-once redelivery
# (documents, finance -- see their tasks.py docstrings). STANDING RULE: every
# Celery task added in the future must be idempotent under at-least-once
# delivery, or must explicitly opt out of this default -- do not assume
# exactly-once execution.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
# Soft limit fires SoftTimeLimitExceeded inside the task ~30s before the hard
# kill -- a DB transaction rolls back cleanly if that exception propagates
# uncaught, but a non-transactional side effect (an HTTP call already sent, a
# file already written) is NOT undone. A task that times out is left in
# whatever state its own durable-work lease/idempotency guarantee provides --
# the same failure shape as a hard-killed worker, not a new one.
CELERY_TASK_TIME_LIMIT = int(os.getenv("CELERY_TASK_TIME_LIMIT", "300"))
CELERY_TASK_SOFT_TIME_LIMIT = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "270"))
CELERY_WORKER_PREFETCH_MULTIPLIER = int(os.getenv("CELERY_WORKER_PREFETCH_MULTIPLIER", "1"))
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BEAT_SCHEDULE = {
    "finance-resweep-unmatched-incoming-payments": {
        "task": "apps.finance.tasks.resweep_unmatched_incoming_payments",
        "schedule": 3600.0,
    },
    "notifications-expand-pending-events": {
        "task": "apps.notifications.tasks.expand_pending_notification_events",
        "schedule": 30.0,
    },
    "notifications-reap-stale-events": {
        "task": "apps.notifications.tasks.reap_stale_notification_events",
        "schedule": 300.0,
    },
    "notifications-dispatch-pending": {
        "task": "apps.notifications.tasks.dispatch_pending_notifications",
        "schedule": 30.0,
    },
    "notifications-reap-stale": {
        "task": "apps.notifications.tasks.reap_stale_notifications",
        "schedule": 300.0,
    },
    "documents-purge-expired": {
        "task": "apps.documents.tasks.purge_expired_documents_task",
        "schedule": 3600.0,
    },
    "reports-generate-pending-exports": {
        "task": "apps.reporting.tasks.generate_pending_report_exports",
        "schedule": 60.0,
    },
    "reports-reap-stale-exports": {
        "task": "apps.reporting.tasks.reap_stale_report_exports",
        "schedule": 300.0,
    },
}

# Pluggable document storage -- local filesystem now (apps.documents.storage.local),
# swappable for a cloud backend later purely via this setting. Root defaults to a
# repo-local directory outside version control; production MUST override to durable
# storage outside the container filesystem.
DOCUMENT_STORAGE_ROOT = os.getenv("DOCUMENT_STORAGE_ROOT", str(BASE_DIR / "document_storage"))
DOCUMENT_STORAGE_BACKEND = os.getenv("DOCUMENT_STORAGE_BACKEND", "apps.documents.storage.local.LocalFilesystemBackend")

if PRODUCTION:
    required = ("DJANGO_SECRET_KEY", "FIELD_ENCRYPTION_KEY", "PUBLIC_BASE_URL", "DJANGO_ALLOWED_HOSTS", "DJANGO_CACHE_URL")
    if any(not os.getenv(name, "").strip() for name in required):
        raise ImproperlyConfigured(
            "Production requires explicit secret, encryption key, public URL, allowed hosts, and cache URL"
        )
    if SECRET_KEY == "development-only-change-me" or len(SECRET_KEY) < 32:
        raise ImproperlyConfigured("Production requires a non-default secret key of at least 32 characters")
    if FIELD_ENCRYPTION_KEY == "tcgm_bXMcNCa925qDWCcoGJCg_UHxRh0N50KsbHtbic=":
        raise ImproperlyConfigured("Production cannot use the development field encryption key")
    parsed_public_url = urlparse(PUBLIC_BASE_URL)
    if parsed_public_url.scheme != "https" or not parsed_public_url.hostname or parsed_public_url.hostname in ("localhost", "127.0.0.1", "::1") or parsed_public_url.query or parsed_public_url.fragment or parsed_public_url.username:
        raise ImproperlyConfigured("Production PUBLIC_BASE_URL must be an external HTTPS URL")
    if "*" in ALLOWED_HOSTS or DATABASES["default"]["ENGINE"] != "django.db.backends.postgresql":
        raise ImproperlyConfigured("Production requires explicit allowed hosts and PostgreSQL")
    # RC Area 1 defect fix: these previously fell back to hardcoded dev
    # defaults (config/settings.py's postgres DATABASES branch) even in
    # production if the operator forgot to set them -- a deploy could
    # silently run against the dev password with zero error.
    if any(not os.getenv(name, "").strip() for name in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_HOST")):
        raise ImproperlyConfigured("Production requires explicit POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_HOST")
    if os.getenv("POSTGRES_PASSWORD") == "school_management_dev":
        raise ImproperlyConfigured("Production cannot use the development database password")
    parsed_cache_url = urlparse(os.getenv("DJANGO_CACHE_URL", ""))
    if parsed_cache_url.scheme not in ("redis", "rediss") or not parsed_cache_url.hostname:
        raise ImproperlyConfigured("Production DJANGO_CACHE_URL must be a redis:// or rediss:// URL with a host")
    from cryptography.fernet import Fernet
    try:
        Fernet(FIELD_ENCRYPTION_KEY)
    except (ValueError, TypeError):
        raise ImproperlyConfigured("FIELD_ENCRYPTION_KEY must be a valid Fernet key") from None
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    # Each independently env-overridable: HSTS with includeSubDomains+preload
    # is strong but only correct once every relevant subdomain is HTTPS-capable
    # -- an operational decision for whoever owns DNS, not something to bake
    # in as a silent assumption. Defaults to the strong posture.
    SECURE_HSTS_SECONDS = int(os.getenv("HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = os.getenv("HSTS_INCLUDE_SUBDOMAINS", "true").lower() == "true"
    SECURE_HSTS_PRELOAD = os.getenv("HSTS_PRELOAD", "true").lower() == "true"
    # Only trust X-Forwarded-Proto when Django is actually deployed behind a
    # TLS-terminating reverse proxy/load balancer that overwrites/strips any
    # client-supplied X-Forwarded-Proto before forwarding -- setting this
    # when Django is directly internet-facing would let a client spoof the
    # header and bypass SECURE_SSL_REDIRECT. The deployment invariant this
    # setting assumes: INTERNET -> trusted reverse proxy/LB -> Django, never
    # INTERNET -> Django directly. Defaults to the confirmed topology but
    # stays a one-line env override, not a hardcoded assumption.
    if os.getenv("BEHIND_REVERSE_PROXY", "true").lower() == "true":
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # DRF throttle counters live in the cache -- Django's default per-process
    # LocMemCache would let each Gunicorn worker independently allow the full
    # configured rate. DJANGO_CACHE_URL is explicit (checked in `required`
    # above), not derived from CELERY_BROKER_URL -- coupling Django's cache
    # config to Celery's URL shape would make a config mistake here silently
    # weaken a security control. config.cache.FailOpenRedisCache (not
    # Django's built-in RedisCache directly -- it has no IGNORE_EXCEPTIONS
    # equivalent) makes the cache, and therefore throttling, fail *open* on
    # a Redis outage: a request that can't reach the cache is allowed
    # through rather than raising a 500 for every authenticated request or,
    # worse, dropping a legitimate M-Pesa callback. Redis HA/monitoring
    # itself is a later hardening pass.
    CACHES = {
        "default": {
            "BACKEND": "config.cache.FailOpenRedisCache",
            "LOCATION": os.getenv("DJANGO_CACHE_URL"),
        }
    }
