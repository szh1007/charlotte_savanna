"""知识库 (L5-a) 的三片: 内部端点 / 仓库语料 / seed 命令.

接缝与既有的 agent 端点用例一致 (Django 测试客户端直接打端点), 认证那三种失败
方式由 `test_agent_api.AGENT_URLS` 那张总表罩住 —— 这里不抄第二份.

三片各自要守的那句话:

- **端点**: 只给启用文章, 字段一个不多; 而且**直查库** —— 改完立刻看得见, 不走
  Redis (L1a 那条「助手看到的必须是当下事实」).
- **语料**: 六篇文章的形状 (slug / 分类 / 正文) 对得上索引脚本与模型的期待.
- **seed 命令**: 幂等, 且**默认不覆盖** Admin 里改过的正文 (`--force` 才覆盖).
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from app.minimall.knowledge_corpus import ARTICLES, DEMO_POISONED_ARTICLE
from app.minimall.models import KnowledgeArticle

TOKEN = "test-internal-token"

# 语料的最小结构: 至少几篇、每篇多少字、正文里要有小节标题 (切分器按它断句)
CORPUS_MIN_ARTICLES = 5
CORPUS_MIN_CHARS = 200


def _url():
    return reverse("minimall_agent:knowledge_articles")


@override_settings(CHARAPP_INTERNAL_TOKEN=TOKEN)
class KnowledgeArticleEndpointTest(TestCase):
    """GET knowledge/articles/ —— 索引脚本读的那一份."""

    def setUp(self):
        self.client = APIClient()

    def get(self):
        return self.client.get(_url(), HTTP_X_INTERNAL_TOKEN=TOKEN)

    def test_requires_token(self):
        """没带令牌 → 403 (三种失败方式在 test_agent_api, 这里只确认接线对了)."""
        self.assertEqual(self.client.get(_url()).status_code, 403)

    def test_returns_only_active_articles(self):
        KnowledgeArticle.objects.create(
            slug="live", title="在架", content="# 在架\n\n正文"
        )
        KnowledgeArticle.objects.create(
            slug="gone", title="下架", content="# 下架\n\n正文", is_active=False
        )
        slugs = [row["slug"] for row in self.get().data]
        self.assertEqual(slugs, ["live"])

    def test_payload_fields_are_exactly_the_contract(self):
        """字段集合是契约: 多给一个 (比如 id) 就等于索引脚本多一处要对齐的东西."""
        KnowledgeArticle.objects.create(
            slug="refund-policy",
            title="退款政策",
            category=KnowledgeArticle.Category.POLICY,
            content="# 退款政策\n\n正文",
        )
        row = self.get().data[0]
        self.assertEqual(
            set(row),
            {"slug", "title", "category", "content", "updated_at"},
        )
        self.assertEqual(row["category"], "policy")

    def test_ordering_follows_sort_order(self):
        KnowledgeArticle.objects.create(
            slug="b", title="B", content="# B", sort_order=2
        )
        KnowledgeArticle.objects.create(
            slug="a", title="A", content="# A", sort_order=1
        )
        self.assertEqual([row["slug"] for row in self.get().data], ["a", "b"])

    def test_reads_are_live_not_cached(self):
        """改完不重启、不重索引, 端点看到的就是新正文 —— 它没走 Redis."""
        article = KnowledgeArticle.objects.create(
            slug="live", title="旧", content="# 旧正文"
        )
        self.assertEqual(self.get().data[0]["content"], "# 旧正文")
        article.content = "# 新正文"
        article.save(update_fields=["content", "updated_at"])
        self.assertEqual(self.get().data[0]["content"], "# 新正文")


class KnowledgeCorpusTest(TestCase):
    """仓库语料本身的形状 (它要能喂进切分器, 也要能喂进模型)."""

    def test_covers_at_least_five_articles(self):
        self.assertGreaterEqual(len(ARTICLES), CORPUS_MIN_ARTICLES)

    def test_slugs_are_unique_and_stable_identifiers(self):
        slugs = [article["slug"] for article in ARTICLES]
        self.assertEqual(len(slugs), len(set(slugs)))
        limit = KnowledgeArticle._meta.get_field("slug").max_length
        for slug in slugs:
            with self.subTest(slug=slug):
                # chunk 主键是 f"{slug}-{序号}": slug 要能进 Milvus 的主键列
                # (VARCHAR(64), 留出序号的位), 长度上限由模型字段那一层管着
                self.assertRegex(slug, r"^[a-z0-9]+(-[a-z0-9]+)*$")
                self.assertLessEqual(len(slug), limit)

    def test_every_category_is_a_model_choice(self):
        valid = set(KnowledgeArticle.Category.values)
        for article in ARTICLES:
            with self.subTest(slug=article["slug"]):
                self.assertIn(article["category"], valid)

    def test_every_article_is_a_markdown_document(self):
        for article in ARTICLES:
            with self.subTest(slug=article["slug"]):
                self.assertTrue(article["title"])
                self.assertGreaterEqual(len(article["content"]), CORPUS_MIN_CHARS)
                # 切分器按 Markdown 标题结构优先切; 没有小节标题的语料切出来的块会很碎
                self.assertIn("\n## ", "\n" + article["content"])
                # 正文不许自己写一级标题: 组装时 title 会拼成 `# {标题}` 放在最前面,
                # 正文再写一个, 检索结果里就会出现两个标题 (看着像 bug)
                self.assertFalse(
                    article["content"].startswith("# "),
                    "正文不要自带一级标题, 见 knowledge_corpus.py 的语料约定",
                )


class SeedKnowledgeCommandTest(TestCase):
    """seed_knowledge: 幂等 + 默认不覆盖 (Admin 里改过的正文是当下事实)."""

    def run_seed(self, *args):
        out = StringIO()
        call_command("seed_knowledge", *args, stdout=out)
        return out.getvalue()

    def test_creates_all_articles_from_corpus(self):
        self.run_seed()
        self.assertEqual(KnowledgeArticle.objects.count(), len(ARTICLES))

    def test_second_run_creates_nothing(self):
        self.run_seed()
        before = set(KnowledgeArticle.objects.values_list("slug", flat=True))
        self.run_seed()
        after = set(KnowledgeArticle.objects.values_list("slug", flat=True))
        self.assertEqual(before, after)

    def test_existing_article_is_kept_by_default(self):
        first = ARTICLES[0]
        KnowledgeArticle.objects.create(
            slug=first["slug"], title="Admin 改过的标题", content="# 改过的正文"
        )
        output = self.run_seed()
        kept = KnowledgeArticle.objects.get(slug=first["slug"])
        self.assertEqual(kept.title, "Admin 改过的标题")
        self.assertEqual(kept.content, "# 改过的正文")
        self.assertIn("未动", output)

    def test_force_overwrites_existing_article(self):
        first = ARTICLES[0]
        KnowledgeArticle.objects.create(
            slug=first["slug"], title="Admin 改过的标题", content="# 改过的正文"
        )
        self.run_seed("--force")
        restored = KnowledgeArticle.objects.get(slug=first["slug"])
        self.assertEqual(restored.title, first["title"])
        self.assertEqual(restored.content, first["content"])

    # ---- 演示用的投毒文档 (L5-d): 默认不装, 装了要说清 ------------------------

    def test_the_poisoned_demo_article_is_not_seeded_by_default(self):
        """默认一篇不多 —— 干净知识库里不该躺着一份带指令的文档.

        它是**攻击面的样本**, 不是政策语料: 常驻在库里, 下一个看这个演示机的人会
        分不出它为什么在那儿 (而它会在"现在有什么活动"这种问题里被检索到).
        """
        self.run_seed()

        self.assertFalse(
            KnowledgeArticle.objects.filter(slug=DEMO_POISONED_ARTICLE["slug"]).exists()
        )
        self.assertEqual(KnowledgeArticle.objects.count(), len(ARTICLES))

    def test_the_demo_poison_flag_seeds_it_and_says_so(self):
        """带上 `--demo-poison` 才装, 而且输出里单列一行说明它是谁.

        那一行不是装饰: seed 的输出是运维 / 演示者唯一会看的记录, 混在总数里就
        等于没说.
        """
        output = self.run_seed("--demo-poison")

        self.assertTrue(
            KnowledgeArticle.objects.filter(slug=DEMO_POISONED_ARTICLE["slug"]).exists()
        )
        self.assertIn(DEMO_POISONED_ARTICLE["slug"], output)
        self.assertIn("投毒", output)

    def test_the_demo_poison_run_is_idempotent_too(self):
        """带 flag 连着跑两次同样一篇不新建 (与默认那条路同一条纪律)."""
        self.run_seed("--demo-poison")
        self.run_seed("--demo-poison")

        self.assertEqual(KnowledgeArticle.objects.count(), len(ARTICLES) + 1)

    def test_the_poisoned_demo_article_is_a_valid_document(self):
        """它也得过语料那一关 (能被切分、能被检索) —— 演习的前提是它真的是语料.

        形状检查与 `ARTICLES` 那批同款 (slug 合规 / 有 `##` 小节 / 不自带一级标题);
        "正文里那几条注入规则认不认得出"归业务侧 (`CharApp/tests/test_injection.py`,
        规则表住在那边).
        """
        article = DEMO_POISONED_ARTICLE

        self.assertRegex(article["slug"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertIn(article["category"], KnowledgeArticle.Category.values)
        self.assertIn("\n## ", "\n" + article["content"])
        self.assertFalse(article["content"].startswith("# "))
