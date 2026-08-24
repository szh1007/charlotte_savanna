from django.urls import path

from .views_api import (
    DashboardActivityView,
    DashboardMasteryView,
    DashboardWeakpointsView,
    HealthView,
    JourneyContentView,
    JourneyDetailView,
    JourneyGraphView,
    JourneyListView,
    JourneyReportView,
    JourneyStatusView,
    KbDeletedDocIdsView,
    KbDocumentContentView,
    KbMetaView,
    KnowledgeBaseDetailView,
    KnowledgeBaseDocumentRestoreView,
    KnowledgeBaseDocumentsView,
    KnowledgeBaseDocumentView,
    KnowledgeBaseIndexClaimView,
    KnowledgeBaseIndexFailedView,
    KnowledgeBaseIndexSaveView,
    KnowledgeBaseListView,
    KnowledgeBaseOfflineView,
    KnowledgeBaseOnlineView,
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
    QuestionFlagView,
    RegisterView,
    SessionView,
    SkillTreeView,
    StatusSummaryInputView,
    StreakFreezeView,
    TopicsView,
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
    # 题目反馈标记 (Issue 14, SPEC §7.3 ③)
    path("questions/<int:pk>/flag/", QuestionFlagView.as_view(), name="question-flag"),
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
    # 知识库 (Issue 09, PRD C-1~C-4)
    path("kb/", KnowledgeBaseListView.as_view(), name="kb-list"),
    path("kb/<int:pk>/", KnowledgeBaseDetailView.as_view(), name="kb-detail"),
    path(
        "kb/<int:pk>/documents/",
        KnowledgeBaseDocumentsView.as_view(),
        name="kb-documents",
    ),
    path(
        "kb/documents/<int:pk>/",
        KnowledgeBaseDocumentView.as_view(),
        name="kb-document",
    ),
    path(
        "kb/documents/<int:pk>/restore/",
        KnowledgeBaseDocumentRestoreView.as_view(),
        name="kb-document-restore",
    ),
    path("kb/<int:pk>/offline/", KnowledgeBaseOfflineView.as_view(), name="kb-offline"),
    path("kb/<int:pk>/online/", KnowledgeBaseOnlineView.as_view(), name="kb-online"),
    path("topics/", TopicsView.as_view(), name="topics"),
    # 索引任务内部端点 (FastAPI → Django, X-Internal-Token 认证)
    path(
        "kb/<int:pk>/index-claim/",
        KnowledgeBaseIndexClaimView.as_view(),
        name="kb-index-claim",
    ),
    path(
        "kb/<int:pk>/index-save/",
        KnowledgeBaseIndexSaveView.as_view(),
        name="kb-index-save",
    ),
    path(
        "kb/<int:pk>/index-failed/",
        KnowledgeBaseIndexFailedView.as_view(),
        name="kb-index-failed",
    ),
    # Issue 10 内部端点: 文档内容 (索引解析输入) / 软删清单 (检索过滤)
    path(
        "kb/documents/<int:pk>/content/",
        KbDocumentContentView.as_view(),
        name="kb-document-content",
    ),
    path(
        "kb/<int:pk>/deleted-doc-ids/",
        KbDeletedDocIdsView.as_view(),
        name="kb-deleted-doc-ids",
    ),
    # Issue 11 内部端点: 知识库元信息 (kb 旅程管道解析输入)
    path("kb/<int:pk>/meta/", KbMetaView.as_view(), name="kb-meta"),
    # 分析 Dashboard (Issue 12, DESIGN.md §4.1)
    path(
        "dashboard/mastery/",
        DashboardMasteryView.as_view(),
        name="dashboard-mastery",
    ),
    path(
        "dashboard/activity/",
        DashboardActivityView.as_view(),
        name="dashboard-activity",
    ),
    path(
        "dashboard/weakpoints/",
        DashboardWeakpointsView.as_view(),
        name="dashboard-weakpoints",
    ),
    # 状态总结聚合输入 (Issue 13, FastAPI → Django, X-Internal-Token 认证)
    path(
        "users/<int:pk>/status-summary-input/",
        StatusSummaryInputView.as_view(),
        name="status-summary-input",
    ),
]
