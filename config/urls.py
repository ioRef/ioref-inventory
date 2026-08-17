from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("api.urls")),
    # Public read-only browsing. Last, so it cannot shadow /admin or /api.
    path("", include("inventory.urls")),
]
