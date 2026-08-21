import hashlib
import secrets

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Custom user, defined at project start because it cannot be added later.

    `username` holds the eduPersonPrincipalName, `user@andrew.cmu.edu` at CMU,
    rather than a bare Andrew ID. eppn is unique across the federation where
    a bare username is unique only within one institution, and it maps directly
    onto Entra's UPN when that migration happens. Django's default username
    validator already permits `@`, so this needs no field override.

    `subject_id` holds an opaque, permanent identifier from the IdP when one is
    released (eduPersonUniqueId, or the SAML persistent NameID). eppn may be
    reassigned after a person leaves, so matching on it alone would eventually
    hand a new person an old account's history. When present, subject_id is
    the identity that is matched on and the eppn is treated as a mutable label.

    Optional by design: not every deployment's IdP releases such an attribute,
    and the app degrades to matching on eppn alone.
    """

    subject_id = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        db_index=True,
        help_text="Opaque permanent identifier from the identity provider.",
    )
    idp = models.CharField(
        max_length=50,
        blank=True,
        help_text="Which AUTH_MODE provisioned this account.",
    )

    def __str__(self):
        return self.get_full_name() or self.username


PREFIX_LENGTH = 8
SECRET_BYTES = 32


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKeyQuerySet(models.QuerySet):
    def usable(self):
        now = timezone.now()
        return self.filter(is_active=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )


class ApiKey(models.Model):
    """A shared secret identifying a calling service, not a person.

    ioref-web holds one of these with READ scope. Keys are stored hashed, so a
    database leak yields hashes rather than working credentials. The plaintext
    exists once, at creation time, and is never recoverable.

    SHA-256 rather than a password hasher is deliberate. These are 32 bytes of
    CSPRNG output, so there is no dictionary to attack and no need to make
    verification slow, and verification happens on every API request.
    """

    class Scope(models.TextChoices):
        READ = "read", "Read only"
        WRITE = "write", "Read and write"

    name = models.CharField(max_length=100, help_text="Which service holds this key.")
    # Sent as the first segment of the token so lookup is an indexed hit rather
    # than a scan-and-compare over every key in the table.
    prefix = models.CharField(max_length=PREFIX_LENGTH, unique=True, db_index=True)
    hashed_key = models.CharField(max_length=64)
    scope = models.CharField(max_length=10, choices=Scope.choices, default=Scope.READ)

    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = ApiKeyQuerySet.as_manager()

    class Meta:
        ordering = ("name",)
        # Django would title-case the class name into "Api key" otherwise.
        verbose_name = "API key"
        verbose_name_plural = "API keys"

    def __str__(self):
        state = "" if self.is_usable else " (unusable)"
        return f"{self.name} [{self.prefix}…]{state}"

    @property
    def is_usable(self) -> bool:
        if not self.is_active:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    @classmethod
    def generate(
        cls, name: str, scope: str = Scope.READ, **kwargs
    ) -> tuple["ApiKey", str]:
        """Create a key, returning it alongside the plaintext token.

        The token is returned exactly once and is not recoverable afterwards.
        """
        prefix = secrets.token_hex(PREFIX_LENGTH)[:PREFIX_LENGTH]
        secret = secrets.token_urlsafe(SECRET_BYTES)
        token = f"{prefix}.{secret}"
        key = cls.objects.create(
            name=name, scope=scope, prefix=prefix, hashed_key=hash_key(token), **kwargs
        )
        return key, token

    @classmethod
    def authenticate(cls, token: str) -> "ApiKey | None":
        """Resolve a raw token to a usable key, or None."""
        prefix, _, _ = token.partition(".")
        if not prefix:
            return None
        key = cls.objects.usable().filter(prefix=prefix).first()
        if key is None:
            return None
        # Constant-time: a timing oracle here would leak the stored hash.
        if not secrets.compare_digest(key.hashed_key, hash_key(token)):
            return None
        return key

    def mark_used(self):
        # update_fields so concurrent API calls cannot clobber unrelated columns.
        self.last_used_at = timezone.now()
        self.save(update_fields=["last_used_at"])
