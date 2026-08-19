from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    GroupViewSet,
    HealthViewSet,
    LocationViewSet,
    PartViewSet,
    TagViewSet,
)

router = DefaultRouter()
router.register("parts", PartViewSet, basename="part")
router.register("locations", LocationViewSet, basename="location")
router.register("groups", GroupViewSet, basename="group")
router.register("tags", TagViewSet, basename="tag")
router.register("health", HealthViewSet, basename="health")

# Versioned from day one: ioref-web is a separate repo on its own release
# cycle, so breaking changes here need a path that lets the two deploy apart.
urlpatterns = [path("v1/", include((router.urls, "v1"), namespace="v1"))]
