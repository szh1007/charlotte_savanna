"""CharPlot API 视图 (DRF).

账号体系 (Issue 02): 注册/登录/登出 + 会话探测 + 个人主页 + 连胜冻结兑换.
旅程链路 (Issue 03): 创建/列表/详情 + FastAPI 内部落库端点 (X-Internal-Token).
闯关答题 (Issue 05): 关卡列表/详情 + 提交答案 + 重开.
"""

import base64
import logging

from django.contrib.auth import login, logout
from django.db import connection
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    CharplotJourney,
    CharplotLevel,
    CharplotProfile,
    CharplotReviewReport,
    CharplotUserEvent,
)
from .permissions import IsInternalService
from .serializers import (
    AnswerRequestSerializer,
    CharplotProfileSerializer,
    JourneyCreateSerializer,
    JourneyDetailSerializer,
    JourneyListSerializer,
    LevelDetailSerializer,
    LevelListSerializer,
    ReviewReportSerializer,
    UserLoginSerializer,
    UserRegisterSerializer,
)
from .services import (
    InsufficientCoinsError,
    JourneyGraphError,
    LevelClearedError,
    LevelFailedError,
    LevelNotCurrentError,
    build_skill_tree,
    buy_streak_freeze,
    create_journey,
    ensure_levels_for_journey,
    mark_journey_failed,
    record_event,
    restart_level,
    save_journey_graph,
    submit_answer,
)

logger = logging.getLogger(__name__)


class HealthView(APIView):
    """健康检查 - 探活 MySQL 与 Redis.

    三端联通的基础链路 (Issue 01): 前端 / 运维可轮询此端点确认 Django 侧就绪.
    """

    # 健康检查无需认证
    authentication_classes = []
    permission_classes = []

    def get(self, request):
        # MySQL: ensure_connection 失败会抛异常, 捕获后标记 db 不健康
        db_status = "ok"
        try:
            connection.ensure_connection()
        except Exception as exc:
            db_status = "error"
            logger.error("CharPlot health: DB check failed: %s", exc)

        # Redis: django-redis 客户端提供 ping()
        redis_status = "ok"
        try:
            from django.core.cache import cache

            cache.client.get_client().ping()
        except Exception as exc:
            redis_status = "error"
            logger.error("CharPlot health: Redis check failed: %s", exc)

        is_ok = db_status == "ok" and redis_status == "ok"
        payload = {
            "status": "ok" if is_ok else "degraded",
            "service": "charplot-django",
            "db": db_status,
            "redis": redis_status,
            "time": timezone.now().isoformat(),
        }
        code = status.HTTP_200_OK if is_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=code)


class SessionView(APIView):
    """会话探测 + CSRF cookie 引导 (SPA 启动时调用).

    ensure_csrf_cookie: 未认证的首次 GET 也会下发 csrftoken cookie,
    是登录 POST 的前置 (middleware 在视图前校验 cookie, 无 cookie 直接 403).
    """

    permission_classes = [AllowAny]

    @method_decorator(ensure_csrf_cookie)
    def get(self, request):
        if request.user.is_authenticated:
            return Response(
                {
                    "authenticated": True,
                    "user": {
                        "id": request.user.id,
                        "username": request.user.username,
                        "email": request.user.email,
                        "is_staff": request.user.is_staff,
                    },
                }
            )
        return Response({"authenticated": False, "user": None})


@method_decorator(csrf_protect, name="dispatch")
class RegisterView(APIView):
    """注册 (未认证端点).

    DRF as_view 会对 Django CSRF middleware 豁免 (csrf_exempt), 未认证请求
    默认无 CSRF 保护; 注册/登录是 login CSRF 攻击面, 显式开启 csrf_protect.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserRegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(
                {"id": user.id, "username": user.username, "email": user.email},
                status=status.HTTP_201_CREATED,
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    """登录 (未认证端点, CSRF 保护理由同 RegisterView)."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = UserLoginSerializer(
            data=request.data, context={"request": request}
        )
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            login(request, user)
            # profile 自动创建兜底: 覆盖 admin 手工建号等老用户
            CharplotProfile.objects.get_or_create(user=user)
            # 登录事件落库 (按自然日去重), 登录天数统计源 (SPEC §8)
            record_event(user, CharplotUserEvent.EventType.LOGIN)
            return Response(
                {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "is_staff": user.is_staff,
                }
            )
        return Response(serializer.errors, status=status.HTTP_401_UNAUTHORIZED)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        profile, _ = CharplotProfile.objects.get_or_create(user=request.user)
        return Response(CharplotProfileSerializer(profile).data)


class StreakFreezeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        profile, _ = CharplotProfile.objects.get_or_create(user=request.user)
        try:
            buy_streak_freeze(profile)
        except InsufficientCoinsError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(
            {"coins": profile.coins, "frozen": profile.freeze_until},
            status=status.HTTP_200_OK,
        )


class JourneyListView(APIView):
    """旅程创建与列表 (DESIGN §4.1).

    GET: 仅本人旅程, 计数 prefetch 避免 N+1, 全量返回 (个人量小, 契约 {journeys[]}).
    POST: JSON (text/link) 或 multipart (file) 创建, 返回 {journey_id, status}.
    """

    permission_classes = [IsAuthenticated]
    # DRF 全局默认分页, 契约要求 {journeys[]} 全量返回
    pagination_class = None

    def get(self, request):
        journeys = (
            CharplotJourney.objects.filter(user=request.user)
            .prefetch_related("chapters__knowledge_points")
            .order_by("-created_at")
        )
        return Response({"journeys": JourneyListSerializer(journeys, many=True).data})

    def post(self, request):
        serializer = JourneyCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        journey = create_journey(
            user=request.user,
            input_type=data["input_type"],
            content=data.get("content", ""),
            source_file=data.get("source_file"),
        )
        return Response(
            {"journey_id": journey.id, "status": journey.status},
            status=status.HTTP_201_CREATED,
        )


class JourneyDetailView(APIView):
    """旅程详情 + 图谱 + 任务状态 (DESIGN §4.1). 非本人旅程返回 404 不泄露存在性."""

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        journey = get_object_or_404(CharplotJourney, pk=pk, user=request.user)
        return Response(JourneyDetailSerializer(journey).data)


class SkillTreeView(APIView):
    """技能树图数据 (DESIGN §4.1): 节点 (点亮状态/进度) + 依赖边.

    闯关地图页渲染源 (PRD D-1); 点亮状态由服务层从关卡数据聚合计算
    (Issue 05): 已通关点亮, 有关卡进行中 in_progress, 依赖未满足锁定.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        journey = get_object_or_404(CharplotJourney, pk=pk, user=request.user)
        return Response(build_skill_tree(journey))


class LevelListView(APIView):
    """关卡列表 (DESIGN §4.1, GET /api/charplot/journeys/{id}/levels/).

    懒创建: 首次进入为无关卡的知识点生成关卡 (stub 题目, Issue 05),
    幂等, 图谱重生成新增的知识点自动补关卡. 全部关卡返回, 前端按需过滤.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get(self, request, pk):
        journey = get_object_or_404(CharplotJourney, pk=pk, user=request.user)
        ensure_levels_for_journey(journey)
        levels = journey.levels.select_related(
            "knowledge_point__chapter"
        ).prefetch_related("questions")
        return Response({"levels": LevelListSerializer(levels, many=True).data})


def get_level_for_user(pk, user):
    """关卡归属校验: 非本人旅程返回 404, 不泄露存在性 (同旅程详情)."""
    return get_object_or_404(
        CharplotLevel.objects.select_related("knowledge_point__chapter", "journey"),
        pk=pk,
        journey__user=user,
    )


class LevelDetailView(APIView):
    """关卡详情 (Issue 05): 进度/心/当前题, 断点续答定位源.

    中途退出再进 (PRD D-2 持久化): 后端从 current_index / hearts 续答,
    前端直接渲染返回的当前题.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        level = get_level_for_user(pk, request.user)
        return Response(LevelDetailSerializer(level).data)


class LevelAnswerView(APIView):
    """提交答案 (DESIGN §4.1, POST /api/charplot/levels/{id}/answer/).

    判分 + 讲解/来源 + 心动值扣减 + 通关结算, 全部在服务层事务内完成;
    业务异常 (已通关/心扣完/题目不匹配) 映射 400 中文 detail.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        level = get_level_for_user(pk, request.user)
        serializer = AnswerRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        try:
            result = submit_answer(
                level=level,
                question_id=data["question_id"],
                answer=data["answer"],
                duration=data.get("duration", 0),
            )
        except (LevelClearedError, LevelFailedError, LevelNotCurrentError) as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result)


class LevelRestartView(APIView):
    """重开关卡 (POST /api/charplot/levels/{id}/restart/).

    5 心扣完本关失败后重开: 心与进度重置 (题目不变, stub 确定性);
    Attempt 历史保留不覆盖; 已通关关卡禁止重开 (防丢通关状态).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        level = get_level_for_user(pk, request.user)
        if level.cleared:
            return Response(
                {"detail": "本关已通关, 无需重开"}, status=status.HTTP_400_BAD_REQUEST
            )
        restart_level(level)
        return Response(LevelDetailSerializer(level).data)


class JourneyReportView(APIView):
    """复盘报告 (DESIGN §4.1, GET /api/charplot/journeys/{id}/report/).

    仅本人旅程可见 (非本人 404 不泄露存在性); 未通关无报告 → 404, 前端
    据旅程 cleared 状态决定入口显隐. 报告为通关时快照, 只读不改.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        journey = get_object_or_404(CharplotJourney, pk=pk, user=request.user)
        report = CharplotReviewReport.objects.filter(journey=journey).first()
        if report is None:
            return Response(
                {"detail": "旅程尚未通关, 暂无复盘报告"},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(ReviewReportSerializer(report).data)


class JourneyGraphView(APIView):
    """图谱落库 (内部端点, FastAPI → Django, CONTRACT.md §3).

    DRF APIView 默认 csrf_exempt, 免 CSRF 校验; 认证靠 X-Internal-Token.
    落库先删后建, 重复调用幂等 (重试语义).
    """

    authentication_classes = []
    permission_classes = [IsInternalService]

    def post(self, request, pk):
        journey = get_object_or_404(CharplotJourney, pk=pk)
        task_id = (request.data.get("task_id") or "").strip()
        graph = request.data.get("graph")
        if not task_id or graph is None:
            return Response(
                {"detail": "缺少 task_id 或 graph"}, status=status.HTTP_400_BAD_REQUEST
            )
        try:
            save_journey_graph(journey, task_id, graph)
        except JourneyGraphError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"status": "ready"})


class JourneyContentView(APIView):
    """源文件内容获取 (内部端点, FastAPI → Django, CONTRACT.md §5).

    Issue 07 真实管道解析 file 输入: FastAPI 经此端点取文件二进制
    (base64 编码, 支持 pdf/docx 等二进制格式), 解析在 FastAPI 侧完成
    (AI 能力端职责). 无源文件 → 404; 文件缺失 (磁盘已清理) → 400.
    """

    authentication_classes = []
    permission_classes = [IsInternalService]

    def get(self, request, pk):
        journey = get_object_or_404(CharplotJourney, pk=pk)
        if not journey.source_file:
            return Response(
                {"detail": "旅程无源文件"}, status=status.HTTP_404_NOT_FOUND
            )
        try:
            journey.source_file.open("rb")
            raw = journey.source_file.read()
        except (FileNotFoundError, OSError) as exc:
            logger.warning("读取源文件失败 (journey=%s): %s", pk, exc)
            return Response(
                {"detail": "源文件读取失败"}, status=status.HTTP_400_BAD_REQUEST
            )
        finally:
            journey.source_file.close()
        return Response(
            {
                "filename": journey.source_file.name.rsplit("/", 1)[-1],
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }
        )


class JourneyStatusView(APIView):
    """任务失败标记 (内部端点, FastAPI → Django)."""

    authentication_classes = []
    permission_classes = [IsInternalService]

    def post(self, request, pk):
        journey = get_object_or_404(CharplotJourney, pk=pk)
        task_id = (request.data.get("task_id") or "").strip()
        error_message = (request.data.get("error_message") or "").strip()
        if not task_id:
            return Response(
                {"detail": "缺少 task_id"}, status=status.HTTP_400_BAD_REQUEST
            )
        mark_journey_failed(journey, task_id, error_message)
        return Response({"status": "failed"})
