"""Settings for ioref-inventory.

Every deployment-specific value comes from the environment, because this app is
meant to be run by orgs other than IDeATe. Nothing CMU-specific is hardcoded --
identity provider, database, and hostnames are all configuration.
"""

import sys
from pathlib import Path

import environ
from django.templatetags.static import static
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CSRF_TRUSTED_ORIGINS=(list, []),
    AUTH_MODE=(str, "local"),
    # eppn by default (user@andrew.cmu.edu at CMU), not a bare username: it is
    # unique across the federation and maps onto Entra's UPN later.
    REMOTE_USER_HEADER=(str, "HTTP_EPPN"),
    REMOTE_USER_EMAIL_HEADER=(str, "HTTP_MAIL"),
    REMOTE_USER_NAME_HEADER=(str, "HTTP_DISPLAYNAME"),
    REMOTE_USER_SUBJECT_HEADER=(str, "HTTP_PERSISTENT_ID"),
    SECURE_PROXY=(bool, False),
    PUBLIC_BROWSE=(bool, True),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="dev-only-insecure-key-do-not-deploy")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")

INSTALLED_APPS = [
    # Must precede django.contrib.admin. Unfold overrides its templates, and
    # app order is what decides which copy the template loader finds first.
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "accounts",
    "inventory",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database. SQLite by default for portability, per the brief.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": env.db_url(
        "DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'data' / 'db.sqlite3'}"
    )
}

if DATABASES["default"]["ENGINE"].endswith("sqlite3"):
    # Without WAL, a single stock-count write blocks every concurrent reader,
    # which shows up as sporadic "database is locked" under even light use.
    DATABASES["default"].setdefault("OPTIONS", {}).update(
        {"transaction_mode": "IMMEDIATE", "init_command": "PRAGMA journal_mode=WAL;"}
    )

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Authentication
#
# AUTH_MODE picks how *people* sign in; API keys are always available for
# service callers regardless. Swapping Shibboleth for Entra is meant to be an
# env change plus a proxy reconfiguration, not an application change, which is
# why nothing below imports a SAML or OIDC library at module scope.
#
#   local: Django's own login page. Development, and small spin-out installs.
#   shib:  mod_shib terminates SAML upstream and passes attribute headers.
#   oidc:  Entra or any OIDC provider (requires mozilla-django-oidc).
# ---------------------------------------------------------------------------
AUTH_MODE = env("AUTH_MODE")

# Set at project start deliberately. Django cannot swap this out once there is
# production data without a painful manual migration.
AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

if AUTH_MODE == "shib":
    # Header names differ per site (REMOTE_USER vs eppn vs a custom attribute),
    # so they are configurable rather than assumed.
    REMOTE_USER_HEADER = env("REMOTE_USER_HEADER")
    REMOTE_USER_EMAIL_HEADER = env("REMOTE_USER_EMAIL_HEADER")
    REMOTE_USER_NAME_HEADER = env("REMOTE_USER_NAME_HEADER")
    REMOTE_USER_SUBJECT_HEADER = env("REMOTE_USER_SUBJECT_HEADER")
    MIDDLEWARE.append("accounts.middleware.HeaderAuthenticationMiddleware")
    AUTHENTICATION_BACKENDS.insert(0, "accounts.backends.TrustedHeaderBackend")

elif AUTH_MODE == "oidc":
    INSTALLED_APPS.append("mozilla_django_oidc")
    AUTHENTICATION_BACKENDS.insert(
        0, "mozilla_django_oidc.auth.OIDCAuthenticationBackend"
    )
    OIDC_RP_CLIENT_ID = env("OIDC_RP_CLIENT_ID")
    OIDC_RP_CLIENT_SECRET = env("OIDC_RP_CLIENT_SECRET")
    OIDC_RP_SIGN_ALGO = env("OIDC_RP_SIGN_ALGO", default="RS256")
    OIDC_OP_JWKS_ENDPOINT = env("OIDC_OP_JWKS_ENDPOINT")
    OIDC_OP_AUTHORIZATION_ENDPOINT = env("OIDC_OP_AUTHORIZATION_ENDPOINT")
    OIDC_OP_TOKEN_ENDPOINT = env("OIDC_OP_TOKEN_ENDPOINT")
    OIDC_OP_USER_ENDPOINT = env("OIDC_OP_USER_ENDPOINT")

LOGIN_URL = "admin:login" if AUTH_MODE == "local" else env("LOGIN_URL", default="/")
LOGIN_REDIRECT_URL = "/"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{name}"}
    for name in (
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    )
]

# ---------------------------------------------------------------------------
# Admin theme
#
# IDeATe's house palette, which color-codes parts by category: input green,
# output cyan, controller purple, connector gray, power crimson. Kept whole so
# badges can reuse the vocabulary staff already read on the guides site and in
# the drawio shape library.
# ---------------------------------------------------------------------------
UNFOLD = {
    "SITE_TITLE": "IDeATe ioRef Inventory",
    "SITE_HEADER": "IDeATe",
    "SITE_SUBHEADER": "ioRef Inventory",
    # Sourced from ioref-web's static/icon.svg, the canonical ioref mark; if
    # that changes, re-copy it here. SITE_ICON replaces the SITE_SYMBOL square
    # entirely in Unfold's sidebar template rather than combining with it, so
    # SITE_SYMBOL is gone rather than left set and silently ignored.
    "SITE_ICON": lambda request: static("inventory/icon.svg"),
    # The browser tab, as distinct from SITE_ICON above (the sidebar logo).
    # Same file, different Unfold setting: one glyph shown two places.
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "type": "image/svg+xml",
            "href": lambda request: static("inventory/icon.svg"),
        },
    ],
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "STYLES": [lambda request: static("inventory/theme.css")],
    # Off by default in Unfold. Ctrl/Cmd-K jumps to any part or location without
    # touching the mouse, which is the whole point at a bench.
    "COMMAND": {
        "search_models": True,
        "show_history": True,
    },
    "COLORS": {
        # Unfold's default base is Tailwind slate, which is blue-tinted and
        # reads as navy chrome. ioref.org's grays are pure neutral, so this
        # scale is built from the house values: off-white #fdfdfd,
        # gray1 #f2f2f2, gray2 #c4c4c4, connector #636466, gray3 #4f4f4f,
        # off-black #1d1d1d. Equal R=G=B throughout, so no color cast.
        "base": {
            "50": "253 253 253",
            "100": "242 242 242",
            "200": "238 238 238",
            "300": "196 196 196",
            "400": "168 168 168",
            "500": "138 138 138",
            "600": "99 100 102",
            "700": "79 79 79",
            "800": "51 51 51",
            "900": "29 29 29",
            "950": "18 18 18",
        },
        # Tints and shades of the house input green (#14B04D), exact at 500.
        # Unfold wants space-separated RGB, not hex.
        #
        # 600 is pulled deliberately darker than a smooth ramp would put it
        # (#0c8138, ~5:1 against white rather than the brand green's ~2.4:1).
        # Unfold uses 600 for filled buttons with white labels, and the brand
        # green is too light to carry white text at body size.
        "primary": {
            "50": "238 251 243",
            "100": "213 245 225",
            "200": "173 233 198",
            "300": "119 216 162",
            "400": "64 193 121",
            "500": "20 176 77",
            "600": "12 129 56",
            "700": "10 107 47",
            "800": "10 86 40",
            "900": "9 71 34",
            "950": "3 40 18",
        },
    },
    # Replaces Django's app list on the index with the three exceptions worth
    # acting on. See inventory/dashboard.py and templates/admin/index.html.
    "DASHBOARD_CALLBACK": "inventory.dashboard.dashboard_callback",
    "SIDEBAR": {
        "show_search": True,
        "navigation": [
            {
                "title": "Stock",
                "separator": False,
                "items": [
                    {
                        "title": "Parts",
                        "icon": "memory",
                        "link": lambda r: reverse_lazy(
                            "admin:inventory_part_changelist"
                        ),
                    },
                    {
                        "title": "Groups",
                        "icon": "category",
                        "link": lambda r: reverse_lazy(
                            "admin:inventory_group_changelist"
                        ),
                    },
                    {
                        "title": "Categories",
                        "icon": "account_tree",
                        "link": lambda r: reverse_lazy(
                            "admin:inventory_category_changelist"
                        ),
                    },
                    {
                        "title": "Tags",
                        "icon": "label",
                        "link": lambda r: reverse_lazy(
                            "admin:inventory_tag_changelist"
                        ),
                    },
                    {
                        "title": "Locations",
                        "icon": "shelves",
                        "link": lambda r: reverse_lazy(
                            "admin:inventory_location_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "History",
                "separator": True,
                "items": [
                    {
                        "title": "Stock events",
                        "icon": "history",
                        "link": lambda r: reverse_lazy(
                            "admin:inventory_stockevent_changelist"
                        ),
                    },
                    {
                        "title": "Prices",
                        "icon": "payments",
                        "link": lambda r: reverse_lazy(
                            "admin:inventory_priceobservation_changelist"
                        ),
                    },
                ],
            },
            {
                "title": "Access",
                "separator": True,
                "items": [
                    {
                        "title": "API keys",
                        "icon": "key",
                        "link": lambda r: reverse_lazy(
                            "admin:accounts_apikey_changelist"
                        ),
                        # Keys are a security control, not day-to-day stock work.
                        "permission": lambda r: r.user.is_superuser,
                    },
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": lambda r: reverse_lazy(
                            "admin:accounts_user_changelist"
                        ),
                        "permission": lambda r: r.user.is_superuser,
                    },
                    # Django's auth.Group, which the index page lists under
                    # "Authentication and Authorization". Named at length here
                    # because the sidebar already has a "Groups" three items
                    # up, and that one is a kind of part.
                    {
                        "title": "Permission groups",
                        "icon": "shield_person",
                        "link": lambda r: reverse_lazy("admin:auth_group_changelist"),
                        "permission": lambda r: r.user.is_superuser,
                    },
                ],
            },
        ],
    },
}

# ---------------------------------------------------------------------------
# Public browsing
#
# Read-only HTML views at / so the application is usable with nothing in front
# the deploy-elsewhere case. Prices and suppliers are withheld from anonymous
# visitors regardless; see inventory/views.py.
#
# Set PUBLIC_BROWSE=False to require a login for even that, e.g. where stock
# levels themselves are not public.
# ---------------------------------------------------------------------------
PUBLIC_BROWSE = env("PUBLIC_BROWSE")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "accounts.authentication.ApiKeyAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
        "accounts.authentication.ApiKeyScopePermission",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 100,
}

# ---------------------------------------------------------------------------
# Cookie names
#
# Distinct because the app may share a hostname with another application. Two
# Django deployments on one host both default to sessionid and csrftoken; the
# browser sends both on any request under the deeper path and neither can tell
# which is its own. This is the one part of mounting under a path that the
# proxy cannot solve, since it would have to rewrite Set-Cookie on the way out.
# ---------------------------------------------------------------------------
SESSION_COOKIE_NAME = env("SESSION_COOKIE_NAME", default="inventory_sessionid")
CSRF_COOKIE_NAME = env("CSRF_COOKIE_NAME", default="inventory_csrftoken")

# ---------------------------------------------------------------------------
# Mount point
#
# Set SCRIPT_NAME in the environment when serving under a path rather than at
# the root of a host. Empty is the default and the standalone case. gunicorn
# reads the same variable, strips it from PATH_INFO and puts it in the WSGI
# environ, which is all reverse() needs. Two things need it here as well:
#
# FORCE_SCRIPT_NAME, because WhiteNoise reads that setting rather than the WSGI
# environ to work out which prefix to strip when matching a static request.
#
# STATIC_URL, because it must carry the prefix itself. Django resolves and
# caches STATIC_URL on first access, which happens at startup with no request
# in flight, so a relative value freezes as "/static/" and never picks up the
# script name. {% static %} would then render links that the proxy routes to
# whatever else is mounted at the root of the host.
# ---------------------------------------------------------------------------
SCRIPT_NAME = env("SCRIPT_NAME", default="").rstrip("/")
if SCRIPT_NAME:
    FORCE_SCRIPT_NAME = SCRIPT_NAME

# ---------------------------------------------------------------------------
# Static files / i18n
# ---------------------------------------------------------------------------
STATIC_URL = f"{SCRIPT_NAME}/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# The manifest storage requires a collectstatic run to resolve {% static %}.
# That is right for a deployment and wrong for the test suite, which would
# otherwise fail on any view that renders a stylesheet unless collectstatic had
# been run first.
TESTING = "test" in sys.argv

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if TESTING
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "data" / "media"

LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", default="America/New_York")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Security. These matter because the app runs behind Apache in production.
# ---------------------------------------------------------------------------
if env("SECURE_PROXY"):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    # Off by default: the Apache vhost already 301s :80 to :443, and enabling
    # both means Django redirecting requests Apache has already handled. Turn on
    # if the app is ever exposed without that vhost in front.
    SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)
    # Opt-in: preload is effectively irreversible for the domain, so it should
    # be a deliberate act rather than a default inherited from a template.
    SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
}
