from django.urls import path

from . import views

urlpatterns = [
    path("", views.part_list, name="part_list"),
    path("parts/<str:part_number>/", views.part_detail, name="part_detail"),
    path("export.csv", views.export_csv, name="export_csv"),
]
