"""T13 字幕来源与模型下载联动 (ADR-0006): 双路径 / 缓存 / 配额退还 / 自动下载.

验收: POST /api/summarize 新增 subtitle_source (official/model, 默认
official); official 官方字幕 → 不写缓存; official 空 → 回退模型生成
(模型缺失仅校验, 提示「请先下载模型」, 不自动触发下载); model → 缓存
优先 (全局共享命中, 命中不另扣配额, 创建时先扣命中退还) → 转写 → 写缓存
(分 P 加 _pN 后缀); 模型缺失时 model 路径自动触发下载 (转录进度可见,
任务取消不中断); cleaner 清理过期字幕缓存, 不碰模型本体.
"""

from __future__ import annotations

import threading
import time

import pytest
from backend import cleaner as cleaner_mod
from backend import config, subtitle_cache
from backend.quota import quota as daily_quota
from fastapi.testclient import TestClient
from helpers import find_task, member_headers, wait_until

# 带 BV 号的链接 (BV + 10 位, 缓存按 BV 键; av 号/短链跳过缓存)
BV_URL = "https://www.bilibili.com/video/BV1xx411c7mD"
NO_BV_URL = "https://www.bilibili.com/video/av-summary-test"

FAKE_SEGMENTS = [
    {"start": 0.0, "end": 12.5, "text": "大家好, 今天讲字幕来源切换"},
    {"start": 12.5, "end": 60.0, "text": "官方字幕快路径与模型生成双路径"},
]
FAKE_SUMMARY_MD = """# 视频总结: 测试视频标题
> 时长: 60s

## 视频概述
字幕来源切换

## 章节时间线
### 双路径 (00:00 ~ 01:00)
- 官方字幕
- 模型生成

## 核心要点
- 官方字幕快路径
- 模型生成双路径

## 结论
闭环"""


@pytest.fixture
def fake_meta(monkeypatch):
    """替换解析引擎 + mindmap (创建时元信息解析不触网, 同 test_summarize)."""
    from conftest import FAKE_INFO

    monkeypatch.setattr("backend.downloader._extract", lambda url: dict(FAKE_INFO))
    monkeypatch.setattr(
        "backend.llm.generate_mindmap",
        lambda summary, meta: {"title": "测试视频标题", "chapters": []},
    )


@pytest.fixture
def fake_llm(monkeypatch):
    """替换 LLM 总结流 (不触网)."""

    monkeypatch.setattr(
        "backend.llm.summarize_stream", lambda text, meta: iter([FAKE_SUMMARY_MD])
    )


@pytest.fixture
def fake_subtitle(monkeypatch):
    """替换官方字幕快路径: holder["no_subtitle"] 控制有无 (默认无, 驱动回退)."""

    holder = {"no_subtitle": True}

    def _fake(url: str):
        if holder["no_subtitle"]:
            return None
        return [dict(s) for s in FAKE_SEGMENTS]

    monkeypatch.setattr("backend.subtitle.get_subtitles", _fake)
    return holder


@pytest.fixture
def fake_asr(monkeypatch):
    """替换 ASR 转写: 记录调用 + 上报进度 (默认放行)."""

    calls: list[str] = []

    def _fake(url: str, progress_cb=None, cancel_event=None):
        calls.append(url)
        if progress_cb is not None:
            progress_cb("transcribe", 1.0, "转写完成")
        return [dict(s) for s in FAKE_SEGMENTS]

    monkeypatch.setattr("backend.asr.transcribe", _fake)
    return calls


@pytest.fixture
def fake_model_download(monkeypatch):
    """替换模型下载引擎: 落地就绪文件 + 回调进度, 可阻塞/注入失败.

    返回 (calls, control): calls 记录触发次数; control.gate clear 阻塞
    下载 (观察中间态 / 取消不中断), fail 注入引擎异常.
    """

    from pathlib import Path

    calls: list[str] = []
    gate = threading.Event()
    gate.set()
    fail = {"error": None}

    def _fake(model_id: str, local_dir, callback=None) -> None:
        calls.append(model_id)
        if fail["error"] is not None:
            raise fail["error"]
        gate.wait(timeout=5.0)
        d = Path(local_dir)
        d.mkdir(parents=True, exist_ok=True)
        if callback is not None:
            callback("model.pt", 30, 100)  # 中途进度: 观察下载中 progress
        (d / "config.yaml").write_text("model: fake\n", encoding="utf-8")
        (d / "model.pt").write_bytes(b"fake-model-weights")
        if callback is not None:
            callback("model.pt", 100, 100)

    monkeypatch.setattr("backend.model_downloader._snapshot_download", _fake)
    return calls, {"gate": gate, "fail": fail}


def create_summary(
    client: TestClient,
    url: str = BV_URL,
    subtitle_source: str = "official",
    client_id: str = "test-client",
    member: bool = False,
) -> int:
    """POST /api/summarize (带字幕来源), 断言 200, 返回 task_id."""
    headers = {"X-Client-Id": client_id}
    if member:
        headers.update(member_headers(client))
    resp = client.post(
        "/api/summarize",
        json={"url": url, "subtitle_source": subtitle_source},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["task_id"]


def wait_completed(client: TestClient, task_id: int, timeout: float = 5.0) -> dict:
    """轮询任务直至 completed (调度线程异步)."""
    assert wait_until(
        lambda: find_task(client, task_id)["status"] == "completed", timeout
    )
    return find_task(client, task_id)


def _cache_files(model_assets: dict) -> list:
    """字幕缓存目录内 *.json 文件列表."""
    return sorted(model_assets["subtitles_dir"].glob("*.json"))


def _wipe_model_files(model_assets: dict) -> None:
    """删除模型就绪文件 → 状态回 missing (驱动自动下载/失败路径)."""
    for name in ("config.yaml", "model.pt"):
        (model_assets["model_dir"] / name).unlink(missing_ok=True)


# ----- 字幕来源参数与官方快路径 -----


def test_subtitle_source_validation(client, fake_meta, fake_asr, fake_llm) -> None:
    """非法 subtitle_source 拒绝 422 (参数校验先于配额检查); 合法值 200.

    第二个 POST 创建真实任务 (200): 等任务终态, 防后台 worker 在
    monkeypatch 撤销后跑真实 LLM (OpenAI client 惰性创建, SSL 上下文
    构建慢 1s+, 拖慢后续测试 clean_state 的 join_workers, 测试隔离).
    """
    resp = client.post(
        "/api/summarize", json={"url": BV_URL, "subtitle_source": "unknown"}
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/summarize",
        json={"url": BV_URL, "subtitle_source": "model"},
        headers={"X-Client-Id": "test-client"},
    )
    assert resp.status_code == 200
    wait_completed(client, resp.json()["task_id"])


def test_default_official_subtitle_no_cache_write(
    client, fake_meta, fake_llm, fake_subtitle, fake_asr, model_assets
) -> None:
    """official 快路径: 官方字幕命中 → 秒级完成, 不写字幕缓存, 不调 ASR."""
    fake_subtitle["no_subtitle"] = False
    calls = fake_asr
    task_id = create_summary(client)
    task = wait_completed(client, task_id)
    assert task["subtitle_source"] == "official"
    assert calls == []  # 官方字幕在, 不转写
    assert _cache_files(model_assets) == []  # 官方字幕不缓存 (秒级获取)


def test_official_no_subtitle_falls_back_and_caches(
    client, fake_meta, fake_llm, fake_subtitle, fake_asr, model_assets
) -> None:
    """official 无官方字幕 → 自动回退模型生成: 转写 + 写缓存 (按 BV 键)."""
    calls = fake_asr
    task_id = create_summary(client)
    wait_completed(client, task_id)
    assert calls == [BV_URL]
    files = _cache_files(model_assets)
    assert len(files) == 1
    assert files[0].name == "BV1xx411c7mD.json"  # 单 P 无后缀


def test_official_fallback_model_missing_fails_without_download(
    client,
    fake_meta,
    fake_llm,
    fake_subtitle,
    fake_asr,
    model_assets,
    fake_model_download,
) -> None:
    """回退路径模型缺失: 转录 failed 提示「请先下载模型」, 不自动触发下载.

    验收硬性要求: 官方回退只校验模型存在, 缺失报错引导, 避免隐性消耗
    1GB 流量 (model_downloader 引擎零调用).
    """
    _wipe_model_files(model_assets)
    calls, _ = fake_model_download
    task_id = create_summary(client)
    # 任务终态为 failed (转录失败, 依赖全 blocked): 等待转录子任务失败
    assert wait_until(
        lambda: (
            find_task(client, task_id)["subtasks"]["transcript"]["status"] == "failed"
        ),
        timeout=5.0,
    )
    task = find_task(client, task_id)
    error = task["subtasks"]["transcript"]["error"]
    assert "请先下载模型" in error
    assert calls == []  # 未自动触发模型下载


# ----- model 路径: 缓存优先 + 配额退还 -----


def test_model_source_cache_hit_refunds_quota(
    client, fake_meta, fake_llm, fake_subtitle, fake_asr, model_assets
) -> None:
    """model 路径缓存命中: 不调 ASR + 创建时扣的配额退还 (净消耗 0).

    第一次 (miss): 转写 + 写缓存, 扣 1 不退; 第二次 (命中): 直接完成,
    扣 1 退 1 → 总量仍为 1 (两次总结仅消耗一次配额).
    """
    calls = fake_asr
    first_id = create_summary(client, subtitle_source="model")
    wait_completed(client, first_id)
    assert calls == [BV_URL]
    assert daily_quota._usages["test-client"].summary_count == 1  # miss 不退还

    second_id = create_summary(client, subtitle_source="model")
    task = wait_completed(client, second_id)
    assert task["subtitle_source"] == "model"
    assert len(calls) == 1  # 缓存命中, 未再转写
    # 命中退还: 两次总结只净消耗 1 次配额 (创建扣 2 → 命中退 1)
    assert daily_quota._usages["test-client"].summary_count == 1
    # 命中时转录子任务 message 区分缓存来源
    assert task["subtasks"]["transcript"]["message"] == "字幕获取完成"


def test_model_source_cache_miss_transcribes(
    client, fake_meta, fake_llm, fake_subtitle, fake_asr, model_assets
) -> None:
    """model 路径缓存未命中: 转写 + 写缓存; 后续任务全局共享命中."""
    calls = fake_asr
    first_id = create_summary(client, subtitle_source="model")
    wait_completed(client, first_id)
    assert calls == [BV_URL]
    files = _cache_files(model_assets)
    assert len(files) == 1

    # 不同 client 创建 (全局共享缓存): 命中不调 ASR
    second_id = create_summary(
        client, subtitle_source="model", client_id="other-client"
    )
    wait_completed(client, second_id)
    assert len(calls) == 1


def test_member_model_source_no_refund_needed(
    client, fake_meta, fake_llm, fake_subtitle, fake_asr, model_assets
) -> None:
    """会员不限量: 命中缓存不涉及配额 (无 client_id 计数, 无需退还)."""
    first_id = create_summary(client, subtitle_source="model", member=True)
    wait_completed(client, first_id)
    second_id = create_summary(client, subtitle_source="model", member=True)
    wait_completed(client, second_id)
    assert daily_quota._usages == {}  # 会员不计数


# ----- 缓存键 / TTL / cleaner -----


def test_cache_key_p_suffix() -> None:
    """分 P 缓存隔离: p>1 加 _pN 后缀, 单 P 无后缀, 无 BV 返回 None."""
    assert subtitle_cache.cache_key(BV_URL) == "BV1xx411c7mD"
    assert subtitle_cache.cache_key(f"{BV_URL}?p=2") == "BV1xx411c7mD_p2"
    assert subtitle_cache.cache_key(f"{BV_URL}?p=1") == "BV1xx411c7mD"
    assert subtitle_cache.cache_key(NO_BV_URL) is None  # av 号: 跳过缓存


def test_cache_ttl_cleanup_keeps_model_intact(
    client,
    fake_meta,
    fake_llm,
    fake_subtitle,
    fake_asr,
    model_assets,
    monkeypatch,
) -> None:
    """cleaner 清理过期字幕缓存 (免费 24h), 模型本体文件不受影响.

    过期判定按文件内创建者身份 TTL (与交付 TTL 同源): 注入时钟推进越过
    TTL 后 cleanup_expired 删除缓存, 模型 config.yaml/model.pt 保留.
    """
    tick = {"now": time.time()}
    monkeypatch.setattr(subtitle_cache, "_now", lambda: tick["now"])
    task_id = create_summary(client, subtitle_source="model")
    wait_completed(client, task_id)
    assert len(_cache_files(model_assets)) == 1
    tick["now"] += config.FREE_DELIVERY_TTL + 1  # 越过免费 24h TTL

    cleaner_mod.cleaner.cleanup_expired()
    assert _cache_files(model_assets) == []
    # 模型本体不受清理影响 (持久资产, 独立目录)
    assert (model_assets["model_dir"] / "config.yaml").is_file()
    assert (model_assets["model_dir"] / "model.pt").is_file()


def test_cache_get_expired_returns_miss(
    client, fake_meta, fake_llm, fake_subtitle, fake_asr, model_assets, monkeypatch
) -> None:
    """过期缓存按 miss 处理 (读取不主动删, 不阻塞转录, 重新转写)."""
    tick = {"now": time.time()}
    monkeypatch.setattr(subtitle_cache, "_now", lambda: tick["now"])
    calls = fake_asr
    first_id = create_summary(client, subtitle_source="model")
    wait_completed(client, first_id)
    tick["now"] += config.FREE_DELIVERY_TTL + 1
    second_id = create_summary(client, subtitle_source="model")
    wait_completed(client, second_id)
    assert len(calls) == 2  # 过期缓存 miss → 重新转写


# ----- 模型缺失自动下载联动 -----


def test_model_source_missing_triggers_auto_download(
    client,
    fake_meta,
    fake_llm,
    fake_subtitle,
    fake_asr,
    model_assets,
    fake_model_download,
) -> None:
    """model 路径模型缺失: 自动触发下载 (引擎被调用), 转录进度 0~50 可见.

    验收: 自动下载联动 — 转录子任务显示「模型下载中 x%」, 下载完成后
    继续转写并完成 (进度映射: 模型下载 0~50 → 音频 50~55 → 转写 55~100).
    """
    _wipe_model_files(model_assets)
    calls, control = fake_model_download
    control["gate"].clear()  # 阻塞下载: 观察转录子任务的模型下载中状态
    task_id = create_summary(client, subtitle_source="model")

    def downloading() -> bool:
        task = find_task(client, task_id)
        sub = task["subtasks"]["transcript"]
        return sub["status"] == "running" and "模型下载中" in (sub["message"] or "")

    assert wait_until(downloading, timeout=5.0)
    task = find_task(client, task_id)
    # 进度映射: 模型下载中 (0~50) + 其余子任务未开始 → 转录进度 < 50
    assert task["subtasks"]["transcript"]["progress"] < 50.0
    assert calls == [config.ASR_MODEL]  # 自动触发下载

    control["gate"].set()  # 放行下载 → 模型就绪 → 继续转写
    wait_completed(client, task_id, timeout=10.0)
    assert len(calls) == 1
    # 完成态: 转录 100 (asr 回调已上报)
    assert find_task(client, task_id)["subtasks"]["transcript"]["progress"] == 100.0


def test_task_cancel_does_not_interrupt_model_download(
    client,
    fake_meta,
    fake_llm,
    fake_subtitle,
    fake_asr,
    model_assets,
    fake_model_download,
) -> None:
    """任务取消不中断模型下载: 取消后模型仍下载中, 放行后完成置 ready.

    模型为全局资产 (独立于任务生命周期): 取消只中断转录等待循环
    (子任务 failed「已取消」), 下载线程继续, 最终 ready.
    """
    _wipe_model_files(model_assets)
    calls, control = fake_model_download
    control["gate"].clear()
    task_id = create_summary(client, subtitle_source="model")
    assert wait_until(
        lambda: (
            "模型下载中"
            in (find_task(client, task_id)["subtasks"]["transcript"]["message"] or "")
        ),
        timeout=5.0,
    )
    client.delete(f"/api/tasks/{task_id}")  # 清除记录 = 取消任务

    from backend.model_downloader import model_downloader

    assert model_downloader.status()["status"] == "downloading"  # 下载未被中断
    control["gate"].set()
    assert wait_until(
        lambda: model_downloader.status()["status"] == "ready", timeout=5.0
    )
    assert calls == [config.ASR_MODEL]
