from django.urls import path

from core import views

urlpatterns = [
    path("", views.demo_index),
    path("files/<str:name>/", views.demo_file),
    path("form-a/", views.demo_form_a),
    path("form-a/education/", views.demo_form_a_education),
    path("form-a/upload/", views.demo_form_a_upload),
    path("form-a/review/", views.demo_form_a_review),
    path("form-b/", views.demo_form_b),
    path("parcel/", views.demo_parcel),
    path("studio/", views.demo_studio),
]
