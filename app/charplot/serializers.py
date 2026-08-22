"""CharPlot 序列化器 (Issue 02).

注册/登录对齐 minimall 模式 (Serializer + validate_password + create_user);
Profile 响应直出模型字段 + 统计面板 + 连胜中断警告.
"""

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import IntegrityError
from rest_framework import serializers

from .models import CharplotProfile
from .services import build_profile_stats, get_streak_loss_warning

User = get_user_model()


class UserRegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, min_length=8)

    def validate_password(self, value):
        validate_password(value)
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
