from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()


class Command(BaseCommand):
    """Create or update the accounts allowed into the admin.

    Under AUTH_MODE=shib there is no local login form and TrustedHeaderBackend
    creates nothing, so this is how an account comes to exist. Apache admits
    anyone holding a CMU session; whether they are anybody here is decided by
    the rows this command writes.

    Usernames are eppns. Passing a bare Andrew ID is almost certainly a mistake,
    because the SSO headers carry user@andrew.cmu.edu and would never match it.
    """

    help = "Grant or revoke admin access for one or more eppns."

    def add_arguments(self, parser):
        parser.add_argument(
            "eppn",
            nargs="+",
            help="Full eppn, e.g. merichar@andrew.cmu.edu.",
        )
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Also grant superuser. Needed for the first account.",
        )
        parser.add_argument(
            "--revoke",
            action="store_true",
            help=(
                "Withdraw staff and superuser instead of granting them. The "
                "account is kept, because stock events reference who recorded "
                "them."
            ),
        )

    def handle(self, *args, **options):
        revoke = options["revoke"]

        for raw in options["eppn"]:
            eppn = raw.strip().lower()

            if "@" not in eppn and not revoke:
                self.stderr.write(
                    self.style.WARNING(
                        f"{eppn}: not an eppn, so the SSO headers will never "
                        f"match it. Expected something like {eppn}@andrew.cmu.edu."
                    )
                )
                continue

            user = User.objects.filter(username=eppn).first()
            created = user is None

            if created:
                if revoke:
                    self.stdout.write(f"{eppn}: no account, nothing to revoke")
                    continue
                user = User(username=eppn, idp=settings.AUTH_MODE, is_active=True)
                # The account exists to be matched by the SSO proxy, never to
                # sign in through the local form.
                user.set_unusable_password()

            user.is_staff = not revoke
            if revoke:
                user.is_superuser = False
            elif options["superuser"]:
                user.is_superuser = True
            user.save()

            if revoke:
                verb = "revoked"
            elif created:
                verb = "created, superuser" if user.is_superuser else "created, staff"
            else:
                verb = "updated, superuser" if user.is_superuser else "updated, staff"
            self.stdout.write(self.style.SUCCESS(f"{eppn}: {verb}"))
