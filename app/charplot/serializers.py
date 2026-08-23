"""CharPlot 序列化器 (Issue 02 / 03).

注册/登录对齐 minimall 模式 (Serializer + validate_password + create_user);
Profile 响应直出模型字段 + 统计面板 + 连胜中断警告; 旅程序列化器输出
图谱规范化嵌套 (prerequisites 为 DB 主键 int 列表, CONTRACT.md).
"""

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import (
    validate_password as validate_password_strength,
)
from django.core.validators import URLValidator
from django.db import IntegrityError
from rest_framework import serializers

from .models import (
    CharplotChapter,
    CharplotJourney,
    CharplotKnowledgePoint,
    CharplotProfile,
)
from .services import build_profile_stats, get_streak_loss_warning

User = get_user_model()


class UserRegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_password(self, value):
        validate_password_strength(value)
        return value

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already exists")
        return value

    def validate_email(self, value):
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError("Email already exists")
        return value

    def create(self, validated_data):
        try:
            user = User.objects.create_user(**validated_data)
        except IntegrityError:
            raise serializers.ValidationError("注册失败, 请重试")
        # 注册即建 profile, 游戏化状态载体 (Issue 01 骨架约定)
        CharplotProfile.objects.create(user=user)
        return user


class UserLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, attrs):
        user = authenticate(
            request=self.context.get("request"),
            username=attrs["username"],
            password=attrs["password"],
        )
        if user is None:
            raise serializers.ValidationError("用户名或密码错误")
        if not user.is_active:
            raise serializers.ValidationError("账号已被禁用")
        attrs["user"] = user
        return attrs


class CharplotProfileSerializer(serializers.ModelSerializer):
    """个人主页载荷: 游戏化状态 + 用户信息 + 统计面板 + 连胜警告."""

    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    is_staff = serializers.BooleanField(source="user.is_staff", read_only=True)
    stats = serializers.SerializerMethodField()
    streak_loss_warning = serializers.SerializerMethodField()

    class Meta:
        model = CharplotProfile
        fields = [
            "id",
            "username",
            "email",
            "is_staff",
            "xp",
            "level",
            "streak",
            "max_streak",
            "hearts",
            "coins",
            "last_study_date",
            "freeze_until",
            "stats",
            "streak_loss_warning",
            "created_at",
            "updated_at",
        ]
        # 游戏化状态只读, 变更一律走服务层
        read_only_fields = fields

    def get_stats(self, obj):
        return build_profile_stats(obj.user)

    def get_streak_loss_warning(self, obj):
        return get_streak_loss_warning(obj)


# ---------------------------------------------------------------------------
# 旅程 (Issue 03)
# ---------------------------------------------------------------------------


class JourneyCreateSerializer(serializers.Serializer):
    """创建旅程: JSON (text/link) 或 multipart (file), DRF 按 Content-Type 选解析器."""

    input_type = serializers.ChoiceField(choices=["text", "file", "link"])
    content = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    source_file = serializers.FileField(required=False)

    def validate(self, attrs):
        input_type = attrs["input_type"]
        content = (attrs.get("content") or "").strip()
        if input_type == "file":
            if not attrs.get("source_file"):
                raise serializers.ValidationError("文件输入必须上传 source_file")
            attrs["content"] = ""  # file 输入忽略 content, 文件内容解析是 Issue 07
        else:
            if not content:
                raise serializers.ValidationError("文本/链接输入必须提供 content")
            if input_type == "link":
                URLValidator()(content)  # 非法 URL 抛 ValidationError
        return attrs


class JourneyListSerializer(serializers.ModelSerializer):
    """旅程列表项: 计数由视图 prefetch 后直接 len, 避免 N+1."""

    chapter_count = serializers.SerializerMethodField()
    kp_count = serializers.SerializerMethodField()

    class Meta:
        model = CharplotJourney
        fields = [
            "id",
            "title",
            "input_type",
            "status",
            "cleared",
            "chapter_count",
            "kp_count",
            "created_at",
        ]

    def get_chapter_count(self, obj):
        return len(obj.chapters.all())

    def get_kp_count(self, obj):
        return sum(len(c.knowledge_points.all()) for c in obj.chapters.all())


class KnowledgePointNestedSerializer(serializers.ModelSerializer):
    """知识点嵌套: prerequisites 返回 DB 主键 int 列表 (最小契约), 前端本地映射标题."""

    prerequisites = serializers.PrimaryKeyRelatedField(many=True, read_only=True)

    class Meta:
        model = CharplotKnowledgePoint
        fields = ["id", "title", "summary", "order", "error_score", "prerequisites"]


class ChapterNestedSerializer(serializers.ModelSerializer):
    knowledge_points = KnowledgePointNestedSerializer(many=True, read_only=True)

    class Meta:
        model = CharplotChapter
        fields = ["id", "title", "summary", "order", "knowledge_points"]


class JourneyDetailSerializer(serializers.ModelSerializer):
    """旅程详情: 图谱规范化嵌套; graph 快照不返回 (权威 = chapters 嵌套).

    content 返回输入原文, 供前端失败重试时重新启动管道 (POST /ai/pipeline).
    """

    chapters = ChapterNestedSerializer(many=True, read_only=True)

    class Meta:
        model = CharplotJourney
        fields = [
            "id",
            "title",
            "input_type",
            "content",
            "status",
            "cleared",
            "latest_task_id",
            "error_message",
            "created_at",
            "updated_at",
            "chapters",
        ]
