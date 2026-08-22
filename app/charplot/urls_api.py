from django.urls import path

from .views_api import HealthView

app_name = "charplot_api"

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
]
