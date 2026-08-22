"""CharPlot API 视图 (DRF).

账号体系 (Issue 02): 注册/登录/登出 + 会话探测 + 个人主页 + 连胜冻结兑换.
"""

import logging

from django.contrib.auth import login, logout
from django.db import connection
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CharplotProfile, CharplotUserEvent
from .serializers import (
    CharplotProfileSerializer,
    UserLoginSerializer,
    UserRegisterSerializer,
)
from .services import (
    InsufficientCoinsError,
    buy_streak_freeze,
    record_event,
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
