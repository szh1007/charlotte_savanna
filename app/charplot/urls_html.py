from django.urls import path

from .views_html import ReportShareView

app_name = "charplot_html"

urlpatterns = [
    # 公开分享页 (Issue 06): slug URL, 未登录可访问, 只读 (OG 标签服务端输出)
    path("r/<slug:slug>/", ReportShareView.as_view(), name="report-share"),
]
