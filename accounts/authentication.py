from rest_framework import authentication, exceptions, permissions

from .models import ApiKey

KEYWORD = "Bearer"


class ApiKeyUser:
    """A non-person principal, duck-typed to satisfy DRF and Django templates.

    Deliberately not a real User row: service callers should not appear in the
    staff directory, cannot own stock events, and must never be able to log into
    the admin. StockEvent.recorded_by stays null for API-authored writes, and the
    ApiKey that made the change is recorded on the event's note instead.
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False

    def __init__(self, api_key: ApiKey):
        self.api_key = api_key

    def __str__(self):
        return f"api-key:{self.api_key.name}"

    @property
    def can_write(self) -> bool:
        return self.api_key.scope == ApiKey.Scope.WRITE


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """Authenticate `Authorization: Bearer <prefix>.<secret>`."""

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header or header[0].lower() != KEYWORD.lower().encode():
            return None  # Fall through to session auth for browser users.
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Malformed Authorization header.")

        key = ApiKey.authenticate(header[1].decode())
        if key is None:
            # One message for absent, expired, revoked and wrong keys alike, so
            # the response cannot be used to enumerate valid prefixes.
            raise exceptions.AuthenticationFailed("Invalid API key.")

        key.mark_used()
        return (ApiKeyUser(key), key)

    def authenticate_header(self, request):
        # Without this DRF returns 403 instead of 401 for anonymous requests.
        return KEYWORD


class ApiKeyScopePermission(permissions.BasePermission):
    """Read-scoped keys get safe methods only. Session users are unaffected."""

    message = "This API key is read-only."

    def has_permission(self, request, view):
        user = request.user
        if not isinstance(user, ApiKeyUser):
            return True
        return request.method in permissions.SAFE_METHODS or user.can_write
