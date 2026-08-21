import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend

log = logging.getLogger(__name__)
User = get_user_model()


class TrustedHeaderBackend(BaseBackend):
    """Authenticate a user asserted by an upstream SSO proxy.

    Used with AUTH_MODE=shib, where mod_shib terminates SAML and passes the
    resolved identity down as request headers.

    SECURITY: this backend trusts its input completely. It is only safe when the
    proxy in front of the app unconditionally *overwrites* these headers on every
    request, including requests where the client supplied them. If the app is
    reachable other than through that proxy, anyone can authenticate as anyone by
    setting a header. See accounts/middleware.py, and the deployment section of
    CLAUDE.md for what the proxy in front of this has to guarantee.

    Accounts are never created here. An eppn with no account authenticates as
    nobody, and the request continues anonymously.

    That is a deliberate reversal of provisioning on first sight. Apache admits
    anyone holding a CMU session, because the roster lives in Django rather than
    in `Require shib-user` lines, so first sight is not a colleague arriving: it
    is any of some tens of thousands of people who followed a link. Provisioning
    them would accumulate rows that can never do anything, and worse, would let
    them in: `_may_see_costs` in inventory/views.py turns on `is_authenticated`,
    not on `is_staff`, so a bare account row is enough to reveal prices and
    suppliers that the public views deliberately withhold.

    Use `manage.py grant_staff` to create accounts. It is a manual act for the
    same reason granting permissions is: Shibboleth asserts *who* someone is,
    never that they have any business with the stock.
    """

    def authenticate(self, request, remote_user=None, attributes=None, **kwargs):
        if not remote_user:
            return None

        attributes = attributes or {}
        # eppn, e.g. user@andrew.cmu.edu. Case-insensitive in practice.
        username = remote_user.strip().lower()
        subject_id = (attributes.get("subject_id") or "").strip() or None

        user = self._resolve(username, subject_id)

        if user is None:
            log.info("SSO asserted %s, which has no account here", username)
            return None

        self._sync_attributes(user, attributes)
        return user

    def _resolve(self, username, subject_id):
        """Find the existing account, preferring the permanent identifier.

        eppn can change: a name change, or reassignment after someone leaves.
        Where the IdP releases a permanent subject id, that is the identity and
        the eppn is just a label that follows it. Matching on eppn alone would
        eventually hand a returning stranger someone else's account history.
        """
        if subject_id:
            user = User.objects.filter(subject_id=subject_id).first()
            if user is not None:
                if user.username != username:
                    log.info(
                        "eppn for subject %s changed: %s -> %s",
                        subject_id,
                        user.username,
                        username,
                    )
                    user.username = username
                    user.save(update_fields=["username"])
                return user

        user = User.objects.filter(username=username).first()

        # Backfill on first sight: accounts provisioned before the IdP released
        # a subject id, or created locally, adopt it here.
        if user is not None and subject_id and not user.subject_id:
            user.subject_id = subject_id
            user.save(update_fields=["subject_id"])

        return user

    def _sync_attributes(self, user, attributes):
        """Refresh mutable profile fields, leaving authorization fields alone."""
        changed = []

        email = attributes.get("email", "")
        if email and user.email != email:
            user.email = email
            changed.append("email")

        name = attributes.get("display_name", "")
        if name:
            first, _, last = name.partition(" ")
            if user.first_name != first:
                user.first_name, _ = first, changed.append("first_name")
            if user.last_name != last:
                user.last_name, _ = last, changed.append("last_name")

        if changed:
            user.save(update_fields=changed)

    def get_user(self, user_id):
        return User.objects.filter(pk=user_id).first()
