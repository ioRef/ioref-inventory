from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.backends import TrustedHeaderBackend

User = get_user_model()


class TrustedHeaderBackendTests(TestCase):
    """Identity resolution for AUTH_MODE=shib.

    CMU releases eppn as user@andrew.cmu.edu. Where a permanent subject id is
    also released it wins, because eppn can be reassigned.
    """

    def setUp(self):
        self.backend = TrustedHeaderBackend()

    def auth(self, eppn, subject_id=None, **attrs):
        return self.backend.authenticate(
            None,
            remote_user=eppn,
            attributes={"subject_id": subject_id or "", **attrs},
        )

    def test_provisions_on_first_sight_without_permissions(self):
        user = self.auth("merichar@andrew.cmu.edu", email="merichar@andrew.cmu.edu")
        self.assertEqual(user.username, "merichar@andrew.cmu.edu")
        self.assertTrue(user.is_active)
        # SSO asserts identity, never authorisation.
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        # Must not be able to sign in through the local form.
        self.assertFalse(user.has_usable_password())
        self.assertEqual(user.idp, "shib")

    def test_eppn_is_lowercased(self):
        user = self.auth("MeRiChaR@Andrew.CMU.edu")
        self.assertEqual(user.username, "merichar@andrew.cmu.edu")

    def test_repeat_login_reuses_account(self):
        first = self.auth("merichar@andrew.cmu.edu")
        second = self.auth("merichar@andrew.cmu.edu")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(User.objects.count(), 1)

    def test_subject_id_survives_an_eppn_change(self):
        """A rename must follow the person, not orphan their account."""
        before = self.auth("oldname@andrew.cmu.edu", subject_id="urn:cmu:0001")
        after = self.auth("newname@andrew.cmu.edu", subject_id="urn:cmu:0001")

        self.assertEqual(before.pk, after.pk)
        self.assertEqual(User.objects.count(), 1)
        after.refresh_from_db()
        self.assertEqual(after.username, "newname@andrew.cmu.edu")

    def test_recycled_eppn_does_not_inherit_the_old_account(self):
        """Different subject, same eppn: a new person, so a new account.

        This is the case matching on eppn alone would get wrong -- someone
        leaves, their eppn is reissued, and the newcomer silently inherits
        their history.
        """
        original = self.auth("shared@andrew.cmu.edu", subject_id="urn:cmu:0001")
        original.username = "departed@andrew.cmu.edu"
        original.save()

        newcomer = self.auth("shared@andrew.cmu.edu", subject_id="urn:cmu:0002")
        self.assertNotEqual(original.pk, newcomer.pk)
        self.assertEqual(User.objects.count(), 2)

    def test_subject_id_is_backfilled_onto_existing_accounts(self):
        """Accounts predating subject-id release adopt it on next login."""
        existing = self.auth("merichar@andrew.cmu.edu")
        self.assertIsNone(existing.subject_id)

        again = self.auth("merichar@andrew.cmu.edu", subject_id="urn:cmu:0001")
        self.assertEqual(existing.pk, again.pk)
        again.refresh_from_db()
        self.assertEqual(again.subject_id, "urn:cmu:0001")

    def test_falls_back_to_eppn_when_no_subject_released(self):
        first = self.auth("merichar@andrew.cmu.edu")
        second = self.auth("merichar@andrew.cmu.edu")
        self.assertEqual(first.pk, second.pk)

    def test_attributes_refresh_but_permissions_do_not(self):
        user = self.auth("merichar@andrew.cmu.edu", email="old@cmu.edu")
        user.is_staff = True
        user.save()

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
