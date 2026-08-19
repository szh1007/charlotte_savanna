"""ADR-0004 域名白名单校验验收测试 (resolve / downloads 路由共用)."""

import pytest
from fastapi.testclient import TestClient

ALLOWED = [
    "https://www.bilibili.com/video/av1",
    "https://bilibili.com/video/av2",
    "https://m.bilibili.com/video/av3",
    "https://player.bilibili.com/player.html?bvid=xxx",
    "https://b23.tv/abcdEF",
    "https://WWW.BILIBILI.COM/video/av4",  # 大小写不敏感
]

REJECTED = [
    "https://example.com/v",
    "https://www.youtube.com/watch?v=test",
    "https://bilibili.com.evil.com/v",  # 子域伪造
    "https://b23.tv.evil.com/v",
    "https://douyin.com/video/x",
]


@pytest.mark.parametrize("url", ALLOWED)
def test_resolve_allows_bilibili_domains(
    client: TestClient, fake_extract: list[str], url: str
) -> None:
    """bilibili.com 主域/子域与 b23.tv 短链均放行, 正常进入解析链路."""
    resp = client.post("/api/resolve", json={"url": url})
    assert resp.status_code == 200


@pytest.mark.parametrize("url", REJECTED)
def test_resolve_rejects_non_bilibili_domains(client: TestClient, url: str) -> None:
    """非哔哩哔哩域名一律拒绝: 422 + 明确文案, 不触达引擎."""
    resp = client.post("/api/resolve", json={"url": url})
    assert resp.status_code == 422
    assert "仅支持哔哩哔哩" in resp.json()["detail"]


@pytest.mark.parametrize("url", REJECTED)
def test_create_download_rejects_non_bilibili_domains(
    client: TestClient, url: str
) -> None:
    """下载路由同样受域名白名单约束."""
    resp = client.post("/api/downloads", json={"url": url, "format_id": "18"})
    assert resp.status_code == 422
    assert "仅支持哔哩哔哩" in resp.json()["detail"]
