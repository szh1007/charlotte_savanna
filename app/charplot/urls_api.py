from django.urls import path

from .views_api import (
    HealthView,
    LoginView,
    LogoutView,
    ProfileView,
    RegisterView,
    SessionView,
    StreakFreezeView,
)

app_name = "charplot_api"

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    # 账号体系 (Issue 02)
    path("auth/session/", SessionView.as_view(), name="session"),
    path("auth/register/", RegisterView.as_view(), name="register"),
    path("auth/login/", LoginView.as_view(), name="login"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("profile/", ProfileView.as_view(), name="profile"),
    path("profile/streak-freeze/", StreakFreezeView.as_view(), name="streak-freeze"),
]
