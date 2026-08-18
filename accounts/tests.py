import os
from io import StringIO
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings

from accounts.backends import TrustedHeaderBackend

User = get_user_model()

SHIB_MIDDLEWARE = settings.MIDDLEWARE + [
    "accounts.middleware.HeaderAuthenticationMiddleware",
]
SHIB_BACKENDS = [
    "accounts.backends.TrustedHeaderBackend",
    "django.contrib.auth.backends.ModelBackend",
]


class TrustedHeaderBackendTests(TestCase):
    """Identity resolution for AUTH_MODE=shib.

    CMU releases eppn as user@andrew.cmu.edu. Where a permanent subject id is
    also released it wins, because eppn can be reassigned.

    The backend resolves accounts, it does not create them, so every test here
    starts from an account that grant_staff would have made.
    """

    def setUp(self):
        self.backend = TrustedHeaderBackend()

    def account(self, eppn, **fields):
        user = User(username=eppn, idp="shib", is_active=True, **fields)
        user.set_unusable_password()
        user.save()
        return user

    def auth(self, eppn, subject_id=None, **attrs):
        return self.backend.authenticate(
            None,
            remote_user=eppn,
            attributes={"subject_id": subject_id or "", **attrs},
        )

    def test_an_unknown_eppn_authenticates_nobody(self):
        """The reason Apache can admit everyone with a CMU session.

        A bare account row is not harmless: _may_see_costs turns on
        is_authenticated, so provisioning a stranger would show them prices and
        suppliers the public views withhold.
        """
        self.assertIsNone(self.auth("stranger@andrew.cmu.edu"))
        self.assertEqual(User.objects.count(), 0)

    def test_known_eppn_authenticates(self):
        self.account("merichar@andrew.cmu.edu", is_staff=True)
        user = self.auth("merichar@andrew.cmu.edu")
        self.assertEqual(user.username, "merichar@andrew.cmu.edu")
        self.assertTrue(user.is_staff)
        # Must not be able to sign in through the local form.
        self.assertFalse(user.has_usable_password())

    def test_eppn_is_lowercased(self):
        self.account("merichar@andrew.cmu.edu")
        user = self.auth("MeRiChaR@Andrew.CMU.edu")
        self.assertEqual(user.username, "merichar@andrew.cmu.edu")

    def test_repeat_login_reuses_account(self):
        self.account("merichar@andrew.cmu.edu")
        first = self.auth("merichar@andrew.cmu.edu")
        second = self.auth("merichar@andrew.cmu.edu")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(User.objects.count(), 1)

    def test_subject_id_survives_an_eppn_change(self):
        """A rename must follow the person, not orphan their account."""
        self.account("oldname@andrew.cmu.edu", subject_id="urn:cmu:0001")
        before = self.auth("oldname@andrew.cmu.edu", subject_id="urn:cmu:0001")
        after = self.auth("newname@andrew.cmu.edu", subject_id="urn:cmu:0001")

        self.assertEqual(before.pk, after.pk)
        self.assertEqual(User.objects.count(), 1)
        after.refresh_from_db()
        self.assertEqual(after.username, "newname@andrew.cmu.edu")

    def test_recycled_eppn_does_not_inherit_the_old_account(self):
        """Different subject, same eppn: a new person, so nobody.

        This is the case matching on eppn alone would get wrong: someone
        leaves, their eppn is reissued, and the newcomer silently inherits
        their history. Now the newcomer simply has no account.
        """
        original = self.account("shared@andrew.cmu.edu", subject_id="urn:cmu:0001")
        original.username = "departed@andrew.cmu.edu"
        original.save()

        self.assertIsNone(self.auth("shared@andrew.cmu.edu", subject_id="urn:cmu:0002"))
        self.assertEqual(User.objects.count(), 1)

    def test_subject_id_is_backfilled_onto_existing_accounts(self):
        """Accounts predating subject-id release adopt it on next login.

        grant_staff writes a username and nothing else, so every account starts
        this way and adopts its subject id the first time its owner signs in.
        """
        existing = self.account("merichar@andrew.cmu.edu")
        self.assertIsNone(existing.subject_id)

        again = self.auth("merichar@andrew.cmu.edu", subject_id="urn:cmu:0001")
        self.assertEqual(existing.pk, again.pk)
        again.refresh_from_db()
        self.assertEqual(again.subject_id, "urn:cmu:0001")

    def test_falls_back_to_eppn_when_no_subject_released(self):
        self.account("merichar@andrew.cmu.edu")
        first = self.auth("merichar@andrew.cmu.edu")
        second = self.auth("merichar@andrew.cmu.edu")
        self.assertEqual(first.pk, second.pk)

    def test_attributes_refresh_but_permissions_do_not(self):
        self.account("merichar@andrew.cmu.edu", email="old@cmu.edu", is_staff=True)

        user = self.auth(
            "merichar@andrew.cmu.edu",
            email="new@cmu.edu",
            display_name="Matt Erichar",
        )
        self.assertEqual(user.email, "new@cmu.edu")
        self.assertEqual(user.first_name, "Matt")
        # Granting access is a manual act; a login must not revoke it.
        self.assertTrue(user.is_staff)

    def test_empty_header_authenticates_nobody(self):
        self.assertIsNone(self.auth(""))
        self.assertIsNone(self.backend.authenticate(None, remote_user=None))
        self.assertEqual(User.objects.count(), 0)


@override_settings(
    MIDDLEWARE=SHIB_MIDDLEWARE,
    AUTHENTICATION_BACKENDS=SHIB_BACKENDS,
    REMOTE_USER_HEADER="HTTP_EPPN",
    REMOTE_USER_EMAIL_HEADER="HTTP_MAIL",
    REMOTE_USER_NAME_HEADER="HTTP_DISPLAYNAME",
    REMOTE_USER_SUBJECT_HEADER="HTTP_PERSISTENT_ID",
)
class HeaderAuthenticationMiddlewareTests(TestCase):
    """The wiring between settings, request headers and the backend.

    The backend tests above hand it an attributes dict directly, which is why
    they stayed green while REMOTE_USER_SUBJECT_HEADER was declared in the env
    schema but never assigned as a setting. The subject id was silently dropped
    in production and resolution fell back to eppn alone. These exercise the
    header names as configured.
    """

    HEADERS = {
        "HTTP_EPPN": "merichar@andrew.cmu.edu",
        "HTTP_MAIL": "merichar@andrew.cmu.edu",
        "HTTP_DISPLAYNAME": "Meg Richards",
        "HTTP_PERSISTENT_ID": "https://idp!https://sp!abc123",
    }

    def account(self):
        user = User(username="merichar@andrew.cmu.edu", idp="shib", is_staff=True)
        user.set_unusable_password()
        user.save()
        return user

    def test_headers_sign_in_a_known_account(self):
        self.account()
        response = self.client.get("/", **self.HEADERS)
        self.assertTrue(response.wsgi_request.user.is_authenticated)

        user = User.objects.get(username="merichar@andrew.cmu.edu")
        self.assertEqual(user.email, "merichar@andrew.cmu.edu")
        self.assertEqual(user.first_name, "Meg")

    def test_a_valid_session_for_a_stranger_stays_anonymous(self):
        """A CMU session is not an account here, and must not become one.

        Apache admits anyone with a session so that the roster can live in
        Django. This is the half that makes that safe.
        """
        response = self.client.get("/", **self.HEADERS)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertEqual(User.objects.count(), 0)

    def test_subject_id_reaches_the_backend(self):
        self.account()
        self.client.get("/", **self.HEADERS)
        user = User.objects.get(username="merichar@andrew.cmu.edu")
        self.assertEqual(user.subject_id, "https://idp!https://sp!abc123")

    @override_settings(REMOTE_USER_SUBJECT_HEADER="")
    def test_unconfigured_subject_header_falls_back_to_eppn(self):
        """An IdP that releases no subject id must still authenticate."""
        self.account()
        self.client.get("/", **self.HEADERS)
        user = User.objects.get(username="merichar@andrew.cmu.edu")
        self.assertIsNone(user.subject_id)

    def test_bearer_token_requests_cannot_assert_an_identity(self):
        self.client.get("/", HTTP_AUTHORIZATION="Bearer k", **self.HEADERS)
        self.assertEqual(User.objects.count(), 0)


class GrantStaffCommandTests(TestCase):
    """The only way an account comes to exist under AUTH_MODE=shib."""

    def run_command(self, *args):
        out = StringIO()
        err = StringIO()
        call_command("grant_staff", *args, stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def test_creates_an_account_that_cannot_use_the_login_form(self):
        self.run_command("merichar@andrew.cmu.edu")
        user = User.objects.get(username="merichar@andrew.cmu.edu")
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.has_usable_password())

    def test_superuser_flag_and_lowercasing(self):
        self.run_command("MeRiChaR@Andrew.CMU.edu", "--superuser")
        user = User.objects.get(username="merichar@andrew.cmu.edu")
        self.assertTrue(user.is_superuser)

    def test_revoke_keeps_the_account(self):
        self.run_command("merichar@andrew.cmu.edu", "--superuser")
        self.run_command("merichar@andrew.cmu.edu", "--revoke")

        user = User.objects.get(username="merichar@andrew.cmu.edu")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        # Stock events reference who recorded them, so the row stays.
        self.assertEqual(User.objects.count(), 1)

    def test_a_bare_andrew_id_is_refused(self):
        """It would never match the eppn the SSO headers carry."""
        _, err = self.run_command("merichar")
        self.assertIn("not an eppn", err)
        self.assertEqual(User.objects.count(), 0)


class ShibSettingsTests(SimpleTestCase):
    """AUTH_MODE=shib must assign every header name the middleware reads.

    REMOTE_USER_SUBJECT_HEADER was declared in the env schema and documented in
    .env.example but never assigned as a setting, so the middleware's getattr
    fell back to the empty string and the subject id was dropped in production.
    Nothing failed: the app authenticates on eppn alone, and only a rename or a
    recycled eppn would have exposed it.

    The settings module is re-executed rather than reloaded, because reloading
    it would rebind the live settings the rest of the suite is running under.
    """

    def test_shib_mode_assigns_every_header_setting(self):
        path = settings.BASE_DIR / "config" / "settings.py"
        namespace = {"__file__": str(path)}
        with mock.patch.dict(os.environ, {"AUTH_MODE": "shib"}):
            exec(compile(path.read_text(), str(path), "exec"), namespace)

        self.assertEqual(namespace["AUTH_MODE"], "shib")
        for name in (
            "REMOTE_USER_HEADER",
            "REMOTE_USER_EMAIL_HEADER",
            "REMOTE_USER_NAME_HEADER",
            "REMOTE_USER_SUBJECT_HEADER",
        ):
            self.assertIn(name, namespace, f"{name} is unset when AUTH_MODE=shib")
