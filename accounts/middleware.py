import logging

from django.conf import settings
from django.contrib import auth
from django.core.exceptions import ImproperlyConfigured

log = logging.getLogger(__name__)


class HeaderAuthenticationMiddleware:
    """Log a user in from SSO headers set by an upstream proxy.

    Modelled on Django's RemoteUserMiddleware but with configurable header names,
    since sites differ on whether the identity arrives as REMOTE_USER, eppn, or a
    site-specific attribute.

    Two behaviours worth knowing about:

    1. If the header is absent on a request but a header-authenticated session
       exists, the session is torn down. Otherwise a Shibboleth logout would
       leave the Django session alive and the user still signed in here.

    2. API-key requests are skipped entirely. A service caller presenting a
       Bearer token must not also be able to assert a user identity by header.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.header = getattr(settings, "REMOTE_USER_HEADER", None)
        if not self.header:
            raise ImproperlyConfigured(
                "REMOTE_USER_HEADER must be set when AUTH_MODE=shib."
            )
        self.email_header = getattr(settings, "REMOTE_USER_EMAIL_HEADER", "")
        self.name_header = getattr(settings, "REMOTE_USER_NAME_HEADER", "")
        # Optional: not every IdP releases a permanent identifier, and the app
        # falls back to matching on eppn alone when it is absent.
        self.subject_header = getattr(settings, "REMOTE_USER_SUBJECT_HEADER", "")

    def __call__(self, request):
        if not hasattr(request, "user"):
            raise ImproperlyConfigured(
                "HeaderAuthenticationMiddleware must come after "
                "AuthenticationMiddleware."
            )

        if request.META.get("HTTP_AUTHORIZATION"):
            return self.get_response(request)

        username = request.META.get(self.header)

        if not username:
            # Only log out sessions that this middleware established, so an
            # unrelated local superuser session is not collateral damage.
            if request.session.get("_header_authenticated"):
                auth.logout(request)
            return self.get_response(request)

        username = username.strip().lower()

        if request.user.is_authenticated:
            if request.user.get_username().lower() == username:
                return self.get_response(request)
            # A different user is now asserted; drop the stale session before
            # adopting the new identity.
            auth.logout(request)

        user = auth.authenticate(
            request,
            remote_user=username,
            attributes={
                "email": request.META.get(self.email_header, ""),
                "display_name": request.META.get(self.name_header, ""),
                "subject_id": request.META.get(self.subject_header, ""),
            },
        )

        if user is not None:
            auth.login(request, user)
            request.session["_header_authenticated"] = True
        else:
            # Routine, not an anomaly: the backend does not create accounts, so
            # anyone with a valid SSO session and no account here lands on this
            # branch and continues anonymously.
            log.info("SSO asserted %r, which resolved to no account", username)

        return self.get_response(request)
