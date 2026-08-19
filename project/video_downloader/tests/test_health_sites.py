"""T01 /api/health 与 /api/sites 验收测试."""

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "video-downloader"


def test_sites_returns_non_empty_site_list(client: TestClient) -> None:
    resp = client.get("/api/sites")
    assert resp.status_code == 200
    body = resp.json()
    # 主流平台清单非空, 且每项含名称 / 图标 / 支持格式 (T09 平台墙数据)
    assert body["sites"], "sites 不应为空"
    assert all(
        "name" in s and "icon" in s and "formats" in s and s["formats"]
        for s in body["sites"]
    )
    # 引擎全量支持数 (营销文案「支持 2000+ 平台」的数据来源)
    assert body["total"] > 0
    # 平台墙核心站点应存在 (B 站 / YouTube)
    names = {s["name"] for s in body["sites"]}
    assert "B 站" in names
    assert "YouTube" in names
