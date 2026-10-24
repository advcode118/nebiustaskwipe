from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("api/", include("core.urls")),
    path("demo/", include("core.demo_urls")),
    path("", RedirectView.as_view(url="/demo/", permanent=False)),
]
