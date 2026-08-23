"""CharPlot 页面视图 (Issue 06): 公开分享页.

分享页为服务端渲染 (爬虫可读 OG 标签), 未登录可访问, 纯只读展示
通关快照; 无任何写端点 → 内容不可篡改 (PRD E-2, DESIGN §4.1 GET /r/{slug}).
"""

from django.shortcuts import get_object_or_404, render
from django.views import View

from .models import CharplotReviewReport


class ReportShareView(View):
    """公开复盘报告页: slug URL, 无登录可看 (PRD E-2).

    OG 社交卡片标签 (og:title/og:description/og:image) 在此输出, 爬虫
    抓取即得完整卡片; 页面正文与报告快照一致, 只读展示.
    """

    def get(self, request, slug):
        report = get_object_or_404(CharplotReviewReport, slug=slug)
        journey = report.journey
        stats = report.stats
        context = {
            "report": report,
            "journey": journey,
            "chapters": report.knowledge_summary.get("chapters", []),
            "stats": stats,
            "per_levels": stats.get("levels", []),
            "og_title": report.og_title,
            "og_description": report.og_description,
            # OG 要求绝对 URL, 模板内 build_absolute_uri 拼接
            "og_image_url": (
                request.build_absolute_uri(report.og_image) if report.og_image else ""
            ),
            "page_url": request.build_absolute_uri(request.get_full_path()),
        }
        return render(request, "charplot/report_share.html", context)
