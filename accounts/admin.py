from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from .models import ApiKey, User

# Django registers Group itself at import time, so it arrives with the stock
# admin's styling. Swapping the base class in is the only way to bring it under
# the theme. Otherwise it looks like a different product.
admin.site.unregister(Group)


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = ("username", "first_name", "last_name", "is_staff", "idp")
    list_filter = BaseUserAdmin.list_filter + ("idp",)
    search_fields = ("username", "first_name", "last_name", "email", "subject_id")

    readonly_fields = ("subject_id", "idp")

    def get_fieldsets(self, request, obj=None):
        """Append an identity section, showing subject_id only when there is one.

        Appended rather than rewritten so upstream changes to the stock
        fieldsets are inherited instead of silently dropped.

        Not every IdP releases a permanent identifier, and where none does the
        field is empty on every account forever. An always-blank field with a
        paragraph of explanation attached asks a question of whoever reads it
        that the page cannot answer, so it appears only once populated.
        """
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None:
            return fieldsets

        if obj.subject_id:
            section = {
                "fields": ("subject_id", "idp"),
                "description": (
                    "subject_id is the identity provider's permanent "
                    "identifier. It takes precedence over the username when "
                    "matching an account at login, so a rename follows the "
                    "person."
                ),
            }
        else:
            section = {"fields": ("idp",)}

        return fieldsets + (("Identity provider", section),)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass


@admin.register(ApiKey)
class ApiKeyAdmin(ModelAdmin):
    list_display = (
        "name",
        "prefix",
        "scope",
        "is_active",
        "last_used_at",
        "expires_at",
    )
    list_filter = ("scope", "is_active")
    readonly_fields = ("prefix", "hashed_key", "created_at", "last_used_at")

    def get_fields(self, request, obj=None):
        if obj is None:
            # The secret does not exist yet, so offer only what generate() needs.
            return ("name", "scope", "expires_at")
        return (
            "name",
            "scope",
            "prefix",
            "is_active",
            "expires_at",
            "created_at",
            "last_used_at",
        )

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)

        # Route creation through generate() so the plaintext is produced once,
        # shown once, and never stored. Editing a key later cannot reveal it.
        key, token = ApiKey.generate(
            name=obj.name, scope=obj.scope, expires_at=obj.expires_at
        )
        obj.pk = key.pk
        self.message_user(
            request,
            f"API key for {key.name}: {token}. Copy it now, it cannot be shown again.",
            level=messages.WARNING,
        )
