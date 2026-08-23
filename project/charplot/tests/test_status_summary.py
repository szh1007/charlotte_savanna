"""LLM 状态总结测试 (Issue 13, DESIGN.md §4.2 步骤 13 / PRD F-4).

覆盖: /ai/report/summary 端点契约 ({user_id} → {summary} markdown) /
错误语义 (缺 user_id 422 / 用户不存在 404 / 聚合获取失败 502 / LLM 未配置
503 / LLM 调用失败 502) / prompt 组装 (聚合裁剪: 章节级正确率 + 薄弱点 +
易错清单进入 prompt, daily 明细不进入). LLM 与 Django 内部端点经
monkeypatch 隔离 (server 内 fetch 为静态绑定, patch server 模块属性).
"""

from tests.conftest import FakeChatModel

from project.charplot.api import django_client
from project.charplot.api import server as server_mod
from project.charplot.pipeline import llm as pipeline_llm
from project.charplot.prompt.status_summary import (
    STATUS_SUMMARY_SYSTEM_PROMPT,
    build_status_summary_prompt,
)

SUMMARY_MARKDOWN = (
    "## 强项\n"
    "- 函数基础章节掌握牢固 (正确率 90%)\n"
    "\n"
    "## 弱项\n"
    "- 装饰器语法正确率 40%, 答错 3 次\n"
    "\n"
    "## 学习建议\n"
    "- 优先复习装饰器语法, 间隔 1 天再练"
)

# 聚合输入 (内部端点返回结构, 与 Dashboard 三个用户端点同构; 含 daily /
# 知识点级明细, 用于断言 prompt 裁剪只取 LLM 所需事实)
AGGREGATE = {
    "mastery": {
        "journeys": [
            {
                "journey_id": 1,
                "title": "Python 装饰器",
                "cleared": False,
                "chapters": [
                    {
                        "chapter_id": 1,
                        "title": "函数基础",
                        "answered": 10,
                        "correct": 9,
                        "accuracy": 90,
                        "duration": 60,
                        "knowledge_points": [
                            {
                                "kp_id": 1,
                                "title": "函数是一等公民",
                                "order": 1,
                                "answered": 6,
                                "correct": 6,
                                "accuracy": 100,
                                "duration": 30,
                                "error_score": 0,
                                "weak": False,
                            },
                            {
                                "kp_id": 2,
                                "title": "闭包",
                                "order": 2,
                                "answered": 4,
                                "correct": 3,
                                "accuracy": 75,
                                "duration": 30,
                                "error_score": 1,
                                "weak": False,
                            },
                        ],
                    },
                    {
                        "chapter_id": 2,
                        "title": "装饰器语法",
                        "answered": 5,
                        "correct": 2,
                        "accuracy": 40,
                        "duration": 60,
                        "knowledge_points": [
                            {
                                "kp_id": 3,
                                "title": "装饰器定义",
                                "order": 1,
                                "answered": 5,
                                "correct": 2,
                                "accuracy": 40,
                                "duration": 60,
                                "error_score": 3,
                                "weak": True,
                            }
                        ],
                    },
                ],
            }
        ]
    },
    "activity": {
        "duration_seconds": 1200,
        "cleared_levels": 2,
        "active_days": 5,
        "streak": 3,
        "max_streak": 4,
        "daily": [
            {"date": "2026-08-11", "answers": 2, "cleared": 0, "active": True},
            {"date": "2026-08-12", "answers": 0, "cleared": 0, "active": False},
        ],
    },
    "weakpoints": {
        "weakpoints": [
            {
                "kp_id": 3,
                "title": "装饰器定义",
                "journey_id": 1,
                "journey_title": "Python 装饰器",
                "chapter_title": "装饰器语法",
                "error_score": 3,
                "days_since_review": 2,
                "priority": 9,
                "priority_level": "high",
                "wrong_count": 3,
            }
        ]
    },
}


async def fake_fetch(user_id: int) -> dict:
    """默认假内部端点: 返回固定聚合 (断言调用参数用)."""
    assert user_id == 1
    return AGGREGATE


def _patch_summary_deps(monkeypatch, model, fetch=fake_fetch):
    """隔离 LLM 与内部端点: fake model + fake fetch 一次性 patch."""
    monkeypatch.setattr(pipeline_llm, "get_chat_model", lambda: model)
    monkeypatch.setattr(server_mod, "fetch_status_summary_input", fetch)


# ---- 端点契约 ----


def test_summary_endpoint_success(client, monkeypatch):
    """契约: {user_id} → 200 {summary: markdown}; LLM 收到完整事实 prompt."""
    model = FakeChatModel(sequence=[("【学习活动】", SUMMARY_MARKDOWN)])
    _patch_summary_deps(monkeypatch, model)

    resp = client.post("/ai/report/summary", json={"user_id": 1})
    assert resp.status_code == 200
    assert resp.json() == {"summary": SUMMARY_MARKDOWN}
    # FakeChatModel.calls 记录 human 消息文本 (完整聚合事实裁剪后)
    assert "【学习活动" in model.calls[0]
    assert "【掌握度" in model.calls[0]
    assert "【易错点清单" in model.calls[0]
    # system prompt 约束三段标题 (前端 markdown 渲染锚点)
    assert "## 强项" in STATUS_SUMMARY_SYSTEM_PROMPT
    assert "## 弱项" in STATUS_SUMMARY_SYSTEM_PROMPT
    assert "## 学习建议" in STATUS_SUMMARY_SYSTEM_PROMPT


def test_summary_missing_user_id_422(client, monkeypatch):
    _patch_summary_deps(monkeypatch, FakeChatModel(sequence=[]))
    resp = client.post("/ai/report/summary", json={})
    assert resp.status_code == 422


def test_summary_user_not_found_404(client, monkeypatch):
    """用户不存在 → 404 (内部端点 404 透传, 与数据/服务错误区分)."""

    async def fetch_404(user_id):
        raise django_client.UserNotFoundError(f"用户 {user_id} 不存在")

    _patch_summary_deps(monkeypatch, FakeChatModel(sequence=[]), fetch=fetch_404)
    resp = client.post("/ai/report/summary", json={"user_id": 999})
    assert resp.status_code == 404
    assert "不存在" in resp.json()["detail"]


def test_summary_aggregate_fetch_failed_502(client, monkeypatch):
    """内部端点网络/服务错误 → 502 (前端可重试)."""

    async def fetch_fail(user_id):
        raise RuntimeError("取状态总结聚合失败 (网络): connect timeout")

    _patch_summary_deps(monkeypatch, FakeChatModel(sequence=[]), fetch=fetch_fail)
    resp = client.post("/ai/report/summary", json={"user_id": 1})
    assert resp.status_code == 502
    assert "聚合失败" in resp.json()["detail"]


def test_summary_llm_not_configured_503(client, monkeypatch):
    """CHARPLOT_DEEPSEEK_MODEL_NAME 未配置 → 503 (配置后重试).

    conftest autouse 已将 get_chat_model patch 成 fake (绕过 config 检查),
    此处模拟真实实现未配置时的行为 (与 pipeline/llm.py 同文案).
    """

    def unconfigured():
        raise RuntimeError("未配置 CHARPLOT_DEEPSEEK_MODEL_NAME, 知识管道不可用")

    monkeypatch.setattr(pipeline_llm, "get_chat_model", unconfigured)
    monkeypatch.setattr(server_mod, "fetch_status_summary_input", fake_fetch)

    resp = client.post("/ai/report/summary", json={"user_id": 1})
    assert resp.status_code == 503
    assert "CHARPLOT_DEEPSEEK_MODEL_NAME" in resp.json()["detail"]


def test_summary_llm_failure_502(client, monkeypatch):
    """LLM 调用失败 → 502 (提示可重试)."""

    class FailingModel:
        async def ainvoke(self, messages, **kwargs):
            raise RuntimeError("API timeout")

    _patch_summary_deps(monkeypatch, FailingModel())
    resp = client.post("/ai/report/summary", json={"user_id": 1})
    assert resp.status_code == 502
    assert "生成状态总结失败" in resp.json()["detail"]


# ---- prompt 组装 ----


def test_prompt_contains_facts_and_trimmed_detail():
    """裁剪正确: 活动汇总 / 章节级正确率 / 薄弱点 / 易错清单进入 prompt,
    daily 明细与知识点级明细不进入 (避免稀释 LLM 关注点)."""
    prompt = build_status_summary_prompt(AGGREGATE)

    assert "学习时长: 20 分钟" in prompt
    assert "通关数: 2 关" in prompt
    assert "当前连胜: 3 天" in prompt
    assert "章节「函数基础」: 正确率 90% (9/10 题)" in prompt
    assert "章节「装饰器语法」: 正确率 40% (2/5 题)" in prompt
    assert "薄弱知识点: 装饰器定义" in prompt
    assert "第 1 名「装饰器定义」" in prompt
    assert "答错 3 次" in prompt
    assert "优先级 high" in prompt
    # 明细不进 prompt (prompt 面向 LLM 判断, 非 UI 渲染)
    assert "2026-08-11" not in prompt
    assert "函数是一等公民" not in prompt  # 非薄弱知识点标题
    assert "daily" not in prompt


def test_prompt_empty_data_handling():
    """无学习数据时提示占位, 不崩 (LLM 会基于占位给出引导性建议)."""
    prompt = build_status_summary_prompt(
        {
            "mastery": {"journeys": []},
            "activity": {
                "duration_seconds": 0,
                "cleared_levels": 0,
                "active_days": 0,
                "streak": 0,
                "max_streak": 0,
                "daily": [],
            },
            "weakpoints": {"weakpoints": []},
        }
    )
    assert "暂无答题记录" in prompt
    assert "暂无易错点" in prompt
    assert "学习时长: 0 秒" in prompt
