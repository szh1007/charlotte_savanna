from django.urls import path

from .views_api import (
    HealthView,
    JourneyContentView,
    JourneyDetailView,
    JourneyGraphView,
    JourneyListView,
    JourneyReportView,
    JourneyStatusView,
    LevelAnswerView,
    LevelDetailView,
    LevelGenerationClaimView,
    LevelGenerationFailedView,
    LevelGenerationSaveView,
    LevelListView,
    LevelRestartView,
    LoginView,
    LogoutView,
    ProfileView,
    RegisterView,
    SessionView,
    SkillTreeView,
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
    # 旅程链路 (Issue 03)
    path("journeys/", JourneyListView.as_view(), name="journey-list"),
    path("journeys/<int:pk>/", JourneyDetailView.as_view(), name="journey-detail"),
    path(
        "journeys/<int:pk>/skill-tree/",
        SkillTreeView.as_view(),
        name="journey-skill-tree",
    ),
    # 闯关答题 (Issue 05)
    path("journeys/<int:pk>/levels/", LevelListView.as_view(), name="level-list"),
    path("levels/<int:pk>/", LevelDetailView.as_view(), name="level-detail"),
    path("levels/<int:pk>/answer/", LevelAnswerView.as_view(), name="level-answer"),
    path("levels/<int:pk>/restart/", LevelRestartView.as_view(), name="level-restart"),
    # 复盘报告 (Issue 06)
    path(
        "journeys/<int:pk>/report/",
        JourneyReportView.as_view(),
        name="journey-report",
    ),
    # 内部端点 (FastAPI → Django, X-Internal-Token 认证)
    path("journeys/<int:pk>/graph/", JourneyGraphView.as_view(), name="journey-graph"),
    path(
        "journeys/<int:pk>/content/",
        JourneyContentView.as_view(),
        name="journey-content",
    ),
    path(
        "journeys/<int:pk>/status/", JourneyStatusView.as_view(), name="journey-status"
    ),
    # 题目生成 (Issue 08, FastAPI → Django)
    path(
        "journeys/<int:pk>/level-generation/",
        LevelGenerationClaimView.as_view(),
        name="level-generation-claim",
    ),
    path(
        "journeys/<int:pk>/level-generation/questions/",
        LevelGenerationSaveView.as_view(),
        name="level-generation-save",
    ),
    path(
        "journeys/<int:pk>/level-generation/failed/",
        LevelGenerationFailedView.as_view(),
        name="level-generation-failed",
    ),
]
