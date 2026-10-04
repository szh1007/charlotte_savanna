"""把仓库里的政策语料装进知识表 (L5-a), **默认只补缺的, 不覆盖已存在的**.

为什么要这条命令: `knowledge_corpus.py` 里的六篇文章是演示与检索的语料 —— 干净
目录 clone 之后, 「知识库里有东西可检索」这一步不该靠人对着屏幕手敲一遍. 它按
`demo_prepare` 那条思路做 (那个命令管买家的演示状态, 本命令管语料). 演示装置
(`sh/charapp_demo.sh` 第 5 步) 与本命令是连着的: seed 之后紧跟一次索引重建 ——
顺序反了的话 (或只有后一半), 索引会对着空表"成功"建出一个空库.

**默认不覆盖**是有意的: 管理员在 Admin 里改过的正文是**当下事实**, 而仓库里那份
是起点. 一条不带参数的 seed 悄悄把 Admin 的改动冲掉, 下一问检索引擎答出来的就是
"库里是旧文" —— 这种错很难在演示现场看出来. 所以:
- 默认: 缺哪篇补哪篇, 已存在的一律不动 (输出里逐篇说明「新建 / 已存在, 未动」)
- `--force`: 连已存在的一起按仓库这份覆盖 (要在干净环境里回到基线时用)

**`--demo-poison` 单独一档** (L5-d): 它会多装一篇**故意投毒**的文档
(`knowledge_corpus.DEMO_POISONED_ARTICLE`) —— 注入防护那一段演示要靠它, 而干净
环境里的知识库不该有它, 所以默认不装、装了就单列一行说清 (见 `_report`).

幂等: 连着跑两次, 第二次一篇都不新建 (输出与数据库都一模一样) —— 带不带
`--demo-poison` 都成立.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from app.minimall.knowledge_corpus import ARTICLES, DEMO_POISONED_ARTICLE
from app.minimall.models import KnowledgeArticle

# 一篇文章的正文与分类从语料里读到之后, 写进模型的哪几个字段 (slug 是查找键,
# 不在这里). 单独列出来是为了让「覆盖时动哪些字段」一眼看得见: 只列这四个,
# 别的字段 (排序 / 启用标记) 一旦被人调过, --force 也不该把它调回去.
WRITABLE_FIELDS = ("title", "category", "content", "sort_order")


class Command(BaseCommand):
    help = "把 knowledge_corpus.py 里的政策语料装进知识表 (幂等; 默认只补缺的)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="已存在的文章也按仓库这份覆盖 (默认保留库里的内容, 见命令 docstring)",
        )
        parser.add_argument(
            "--demo-poison",
            action="store_true",
            help=(
                "连演示用的**投毒文档**一起装 (L5-d 的注入防护演示, 见 "
                "knowledge_corpus.DEMO_POISONED_ARTICLE): 默认不装 —— 干净的知识库"
                "里不该有它, 要演那一段就显式带上 (演示装置走的就是这一条)"
            ),
        )

    def handle(self, *args, **options):
        force = options["force"]
        # 装哪几篇: 默认是那六篇政策语料; 带 `--demo-poison` 再多一篇演示用的投毒
        # 文档 (它单独住在 `DEMO_POISONED_ARTICLE`, 不在 ARTICLES 里 —— 见那份文件
        # 里的说明). 两种情形下面那条写库 / 报账的代码一个字不改.
        articles = ARTICLES
        if options["demo_poison"]:
            articles = (*ARTICLES, DEMO_POISONED_ARTICLE)
        created: list[str] = []
        updated: list[str] = []
        kept: list[str] = []

        for article in articles:
            fields = {name: article[name] for name in WRITABLE_FIELDS}
            existing = KnowledgeArticle.objects.filter(slug=article["slug"]).first()
            if existing is None:
                KnowledgeArticle.objects.create(slug=article["slug"], **fields)
                created.append(article["slug"])
            elif force:
                for name, value in fields.items():
                    setattr(existing, name, value)
                existing.save(update_fields=[*fields, "updated_at"])
                updated.append(article["slug"])
            else:
                kept.append(article["slug"])

        self._report(created, updated, kept, demo_poison=options["demo_poison"])

    def _report(self, created, updated, kept, *, demo_poison: bool) -> None:
        """把「这一跑到底动了什么」写清楚 —— 幂等与否由这几行自己回答.

        `--demo-poison` 那一篇**单列一行**说清它进了库: 它是攻击面的样本, 不是政策
        语料 —— 混在总数里, 下一次看见这些行的人分不出库里为什么多了它.
        """
        for slug in created:
            self.stdout.write(f"新建: {slug}")
        for slug in updated:
            self.stdout.write(f"覆盖: {slug}")
        for slug in kept:
            self.stdout.write(f"已存在, 未动: {slug}")
        total = len(created) + len(updated) + len(kept)
        self.stdout.write(
            self.style.SUCCESS(
                f"语料准备完成: 新建 {len(created)} 篇, 覆盖 {len(updated)} 篇, "
                f"保留 {len(kept)} 篇 (共 {total} 篇)"
            )
        )
        if demo_poison:
            self.stdout.write(
                self.style.WARNING(
                    "其中包含演示用的投毒文档 "
                    f"{DEMO_POISONED_ARTICLE['slug']} —— 它只该出现在演示环境; "
                    "重跑索引之后, 问「现在有什么活动」就会检索到它"
                )
            )
        if created or updated:
            self.stdout.write(
                "改完内容记得重跑索引, 否则检索到的还是旧正文: "
                "python -m CharApp.minimall.knowledge.index"
            )
