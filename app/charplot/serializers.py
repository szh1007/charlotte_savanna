"""CharPlot 序列化器 (Issue 02 / 03 / 05).

注册/登录对齐 minimall 模式 (Serializer + validate_password + create_user);
Profile 响应直出模型字段 + 统计面板 + 连胜中断警告; 旅程序列化器输出
图谱规范化嵌套 (prerequisites 为 DB 主键 int 列表, CONTRACT.md); 关卡
序列化器输出进度/心动值/当前题 (题目不含标准答案, 判分在后端).
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
    CharplotKnowledgeBase,
    CharplotKnowledgeBaseDocument,
    CharplotKnowledgePoint,
    CharplotLevel,
    CharplotProfile,
    CharplotQuestion,
    CharplotQuestionFlag,
    CharplotReviewReport,
)
from .services import (
    build_profile_stats,
    get_streak_loss_warning,
    level_locked,
    level_status,
    validate_kb_document_file,
)

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


class TopicSerializer(serializers.ModelSerializer):
    """主题卡片 (用户端, GET /api/charplot/topics/): 仅就绪知识库.

    定义在旅程序列化器之前 (JourneyDetailSerializer 嵌套引用); 亦用于
    JourneyDetail 的 knowledge_base 展示 (Issue 11).
    """

    class Meta:
        model = CharplotKnowledgeBase
        fields = ["id", "name", "description", "cover"]


class JourneyCreateSerializer(serializers.Serializer):
    """创建旅程: JSON (text/link/kb) 或 multipart (file), DRF 按 Content-Type 选解析器.

    Issue 11: kb 类型必带 kb_id, 知识库必须存在且已就绪 (仅就绪库开旅程).
    """

    input_type = serializers.ChoiceField(choices=["text", "file", "link", "kb"])
    content = serializers.CharField(required=False, allow_blank=True, max_length=20000)
    source_file = serializers.FileField(required=False)
    kb_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        input_type = attrs["input_type"]
        content = (attrs.get("content") or "").strip()
        if input_type == "file":
            if not attrs.get("source_file"):
                raise serializers.ValidationError("文件输入必须上传 source_file")
            attrs["content"] = ""  # file 输入忽略 content, 文件内容解析是 Issue 07
        elif input_type == "kb":
            kb_id = attrs.get("kb_id")
            if not kb_id:
                raise serializers.ValidationError("知识库输入必须提供 kb_id")
            try:
                kb = CharplotKnowledgeBase.objects.get(pk=kb_id)
            except CharplotKnowledgeBase.DoesNotExist:
                raise serializers.ValidationError(f"知识库不存在: {kb_id}")
            if kb.status != CharplotKnowledgeBase.Status.READY:
                raise serializers.ValidationError("知识库未就绪, 暂不可开启旅程")
            attrs["knowledge_base"] = kb  # 视图直接消费, 避免二次查询
            attrs["content"] = ""
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

    content 返回输入原文, 供前端失败重试时重新启动管道 (POST /ai/pipeline);
    kb_id/knowledge_base (Issue 11): kb 旅程重试需 kb_id, 展示需库信息.
    """

    chapters = ChapterNestedSerializer(many=True, read_only=True)
    kb_id = serializers.IntegerField(source="knowledge_base_id", read_only=True)
    knowledge_base = TopicSerializer(read_only=True)

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
            "kb_id",
            "knowledge_base",
        ]


# ---------------------------------------------------------------------------
# 闯关答题 (Issue 05)
# ---------------------------------------------------------------------------


def _boss_title(obj):
    """Boss 关展示标题 (无单点语义): 「{章名} · Boss 挑战」."""
    chapter = obj.chapter or obj.knowledge_point.chapter
    return f"{chapter.title} · Boss 挑战"


class LevelListSerializer(serializers.ModelSerializer):
    """关卡列表项: 进度 / 剩余心 / 状态 / 生成状态 / 解锁, 前端关卡入口渲染.

    Issue 08: boss 关 kp_id=null、kp_title 为章级标题; locked 由
    level_locked 计算 (前置章节 Boss 未通关); questions_status 供前端
    生成中/失败重试三态展示.
    """

    kp_id = serializers.SerializerMethodField()
    kp_title = serializers.SerializerMethodField()
    chapter_title = serializers.SerializerMethodField()
    question_count = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    locked = serializers.SerializerMethodField()

    class Meta:
        model = CharplotLevel
        fields = [
            "id",
            "seq",
            "level_type",
            "kp_id",
            "kp_title",
            "chapter_title",
            "question_count",
            "questions_status",
            "latest_task_id",
            "locked",
            "hearts",
            "current_index",
            "cleared",
            "status",
        ]

    def get_kp_id(self, obj):
        return (
            None
            if obj.level_type == CharplotLevel.LevelType.BOSS
            else obj.knowledge_point_id
        )

    def get_kp_title(self, obj):
        if obj.level_type == CharplotLevel.LevelType.BOSS:
            return _boss_title(obj)
        return obj.knowledge_point.title

    def get_chapter_title(self, obj):
        chapter = obj.chapter or obj.knowledge_point.chapter
        return chapter.title

    def get_question_count(self, obj):
        return obj.questions.count()

    def get_status(self, obj):
        return level_status(obj)

    def get_locked(self, obj):
        return level_locked(obj)


class QuestionBriefSerializer(serializers.ModelSerializer):
    """题目载荷 (不含 answer, 判分在后端; options 仅选择类型使用).

    flagged (Issue 14): 当前用户是否已标记过此题 (去重后的持久化状态),
    供答题页反馈入口恢复「已反馈」展示; 序列化 context 需带 request,
    否则 (内部调用) 恒为 False.
    """

    flagged = serializers.SerializerMethodField()

    class Meta:
        model = CharplotQuestion
        fields = ["id", "question_type", "content", "options", "order", "flagged"]

    def get_flagged(self, obj):
        request = self.context.get("request")
        if request is None or not request.user.is_authenticated:
            return False
        return obj.flags.filter(user=request.user).exists()


class LevelDetailSerializer(serializers.ModelSerializer):
    """关卡详情: 进度/心 + 当前题 (断点续答定位源, Issue 05).

    question 为当前题 (current_index 定位), 通关 / 心扣完 / 已答完 / 题目未
    就绪时返回 null, 前端据 questions_status 与 level_status 区分渲染
    结算 / 重开 / 生成中面板. Issue 08: boss 关 kp_id=null、locked 输出.
    """

    kp_id = serializers.SerializerMethodField()
    kp_title = serializers.SerializerMethodField()
    chapter_id = serializers.SerializerMethodField()
    chapter_title = serializers.SerializerMethodField()
    question_count = serializers.SerializerMethodField()
    status = serializers.SerializerMethodField()
    locked = serializers.SerializerMethodField()
    question = serializers.SerializerMethodField()

    class Meta:
        model = CharplotLevel
        fields = [
            "id",
            "seq",
            "level_type",
            "kp_id",
            "kp_title",
            "chapter_id",
            "chapter_title",
            "question_count",
            "questions_status",
            "latest_task_id",
            "locked",
            "hearts",
            "current_index",
            "cleared",
            "status",
            "question",
        ]

    def get_kp_id(self, obj):
        return (
            None
            if obj.level_type == CharplotLevel.LevelType.BOSS
            else obj.knowledge_point_id
        )

    def get_kp_title(self, obj):
        return (
            _boss_title(obj)
            if obj.level_type == CharplotLevel.LevelType.BOSS
            else obj.knowledge_point.title
        )

    def get_chapter_id(self, obj):
        chapter = obj.chapter or obj.knowledge_point.chapter
        return chapter.id

    def get_chapter_title(self, obj):
        chapter = obj.chapter or obj.knowledge_point.chapter
        return chapter.title

    def get_question_count(self, obj):
        return obj.questions.count()

    def get_status(self, obj):
        return level_status(obj)

    def get_locked(self, obj):
        return level_locked(obj)

    def get_question(self, obj):
        if obj.cleared or obj.hearts <= 0:
            return None
        if obj.questions_status != CharplotLevel.QuestionsStatus.READY:
            return None  # 生成中/失败/待生成: 不暴露旧题
        count = obj.questions.count()
        if count == 0 or obj.current_index >= count:
            return None
        current = obj.questions.order_by("order", "id")[obj.current_index]
        # context 透传 (Issue 14): flagged 字段按当前用户计算
        return QuestionBriefSerializer(current, context=self.context).data


class AnswerRequestSerializer(serializers.Serializer):
    """提交答案载荷: question_id 必须为当前题 (后端校验), duration 可选."""

    question_id = serializers.IntegerField()
    answer = serializers.JSONField()
    duration = serializers.IntegerField(required=False, min_value=0, default=0)


class QuestionFlagRequestSerializer(serializers.Serializer):
    """题目反馈标记载荷 (Issue 14): reason 可选, 空 = 仅标记无原因."""

    reason = serializers.ChoiceField(
        choices=CharplotQuestionFlag.Reason.choices,
        required=False,
        allow_blank=True,
        default="",
    )


# ---------------------------------------------------------------------------
# 复盘报告 (Issue 06)
# ---------------------------------------------------------------------------


class ReviewReportSerializer(serializers.ModelSerializer):
    """复盘报告 (DESIGN §4.1, GET /api/charplot/journeys/{id}/report/).

    快照数据直出 (知识总结 + 答题统计, 生成后不可变); share_url 为相对
    路径, 前端复制时拼 location.origin 得到完整公开链接.
    """

    journey_id = serializers.IntegerField(source="journey.id", read_only=True)
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = CharplotReviewReport
        fields = [
            "id",
            "journey_id",
            "slug",
            "knowledge_summary",
            "stats",
            "og_title",
            "og_description",
            "og_image",
            "share_url",
            "created_at",
        ]

    def get_share_url(self, obj):
        return f"/r/{obj.slug}/"


# ---------------------------------------------------------------------------
# 知识库 (Issue 09, PRD C-1~C-4)
# ---------------------------------------------------------------------------


class KbCreateSerializer(serializers.Serializer):
    """创建知识库 (DESIGN §4.1 POST /api/kb): {name, desc, cover} JSON."""

    name = serializers.CharField(max_length=200)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    cover = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_cover(self, value):
        value = (value or "").strip()
        if value:
            URLValidator()(value)  # 非法 URL 抛 ValidationError
        return value


class KbDocumentsUploadSerializer(serializers.Serializer):
    """文档上传 (multipart, 字段 files 多文件): 逐文件格式校验 (all-or-nothing).

    HTML multipart 解析出的 QueryDict 不能直接喂 ListField, 视图需显式
    构造 data={"files": list(request.FILES.getlist("files"))}.
    """

    files = serializers.ListField(
        child=serializers.FileField(), min_length=1, allow_empty=False
    )

    def validate_files(self, value):
        for uploaded_file in value:
            try:
                validate_kb_document_file(uploaded_file)
            except ValueError as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value


class KbDocumentSerializer(serializers.ModelSerializer):
    """文档项: filename 取存储路径 basename (磁盘路径对前端无意义)."""

    knowledge_base_id = serializers.IntegerField(
        source="knowledge_base.id", read_only=True
    )
    filename = serializers.SerializerMethodField()

    class Meta:
        model = CharplotKnowledgeBaseDocument
        fields = [
            "id",
            "knowledge_base_id",
            "title",
            "filename",
            "file_size",
            "is_deleted",
            "deleted_at",
            "created_at",
        ]

    def get_filename(self, obj):
        return obj.file.name.rsplit("/", 1)[-1]


class KnowledgeBaseListSerializer(serializers.ModelSerializer):
    """知识库列表项 (管理员全部状态 / 用户端仅就绪, 视图按身份过滤).

    document_count 由视图 annotate 注入 (有效文档数, 防 N+1).
    """

    document_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = CharplotKnowledgeBase
        fields = [
            "id",
            "name",
            "description",
            "cover",
            "status",
            "collection_name",
            "latest_task_id",
            "error_message",
            "document_count",
            "created_at",
            "updated_at",
        ]


class KnowledgeBaseDetailSerializer(serializers.ModelSerializer):
    """知识库详情 (管理页): documents 按有效/软删分组 (恢复 UX 用).

    单对象两次小查询可接受; 软删文档标记 is_deleted, 管理列表隐藏于
    有效区、展示于回收区 (验收标准 3).
    """

    document_count = serializers.SerializerMethodField()
    documents = serializers.SerializerMethodField()
    deleted_documents = serializers.SerializerMethodField()

    class Meta:
        model = CharplotKnowledgeBase
        fields = [
            "id",
            "name",
            "description",
            "cover",
            "status",
            "collection_name",
            "latest_task_id",
            "error_message",
            "document_count",
            "documents",
            "deleted_documents",
            "created_at",
            "updated_at",
        ]

    def get_document_count(self, obj):
        return obj.documents.filter(is_deleted=False).count()

    def get_documents(self, obj):
        qs = obj.documents.filter(is_deleted=False).order_by("id")
        return KbDocumentSerializer(qs, many=True).data

    def get_deleted_documents(self, obj):
        qs = obj.documents.filter(is_deleted=True).order_by("-deleted_at", "id")
        return KbDocumentSerializer(qs, many=True).data
