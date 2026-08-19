"""T04 会员鉴权验收测试 (HTTP seam).

密钥校验 / 会话签发 / token 识别 / 24h TTL 过期均通过 HTTP 行为验证;
时间用注入时钟 (backend.auth._now) 推进, 无需真实等待.
"""

import time

import pytest
from backend import auth as auth_mod
from backend import config
from backend.auth import MEMBER_SESSION_TTL
from fastapi.testclient import TestClient

MEMBER_KEY = "test-member-key-2026"


@pytest.fixture(autouse=True)
def member_key(monkeypatch):
    """会员测试统一使用已知密钥 (真实密钥仅存在于 .env, 不入库)."""
    monkeypatch.setattr(config, "MEMBER_KEY", MEMBER_KEY)


def test_member_valid_key_returns_session(client: TestClient) -> None:
    """验收: 正确密钥 → is_member=true + expires_at + 会话 token."""
    resp = client.post("/api/member", json={"key": MEMBER_KEY})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_member"] is True
    assert body["expires_at"] > time.time()
    assert len(body["token"]) > 20  # 随机会话 token 非空


def test_member_invalid_key_rejected(client: TestClient) -> None:
    """验收: 错误密钥 → 401 明确拒绝."""
    resp = client.post("/api/member", json={"key": "wrong-key"})
    assert resp.status_code == 401
    assert "密钥无效" in resp.json()["detail"]


def test_member_empty_key_returns_422(client: TestClient) -> None:
    """空密钥属于格式错误: 422 (不进入密钥校验)."""
    resp = client.post("/api/member", json={"key": ""})
    assert resp.status_code == 422


def test_member_key_not_configured_rejects_all(client: TestClient, monkeypatch) -> None:
    """未配置 MEMBER_KEY (空): 任何密钥都拒绝."""
    monkeypatch.setattr(config, "MEMBER_KEY", "")
    resp = client.post("/api/member", json={"key": "anything"})
    assert resp.status_code == 401
    assert "密钥无效" in resp.json()["detail"]


def test_member_status_identified_by_token(client: TestClient) -> None:
    """验收: 携带 X-Member-Token 被识别为会员, 返回过期时间."""
    token = client.post("/api/member", json={"key": MEMBER_KEY}).json()["token"]
    resp = client.get("/api/member/status", headers={"X-Member-Token": token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_member"] is True
    assert body["expires_at"] > time.time()


def test_member_status_without_token_is_free(client: TestClient) -> None:
    """验收: 无 token 查询 → 免费用户状态."""
    resp = client.get("/api/member/status")
    assert resp.status_code == 200
    assert resp.json() == {"is_member": False, "expires_at": None}


def test_member_status_forged_token_is_free(client: TestClient) -> None:
    """伪造 token 不识别为会员 (视为免费用户, 不报错)."""
    resp = client.get("/api/member/status", headers={"X-Member-Token": "forged"})
    assert resp.status_code == 200
    assert resp.json()["is_member"] is False


def test_member_token_expired_revoked(client: TestClient, monkeypatch) -> None:
    """验收: 会话 token 超时 (24h TTL) 后自动失效, 会员身份收回.

    注入时钟推进越过 TTL, 携带原 token 不再识别; 重新提交密钥可再次解锁.
    """
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(auth_mod, "_now", lambda: clock["now"])

    token = client.post("/api/member", json={"key": MEMBER_KEY}).json()["token"]
    assert (
        client.get("/api/member/status", headers={"X-Member-Token": token}).json()[
            "is_member"
        ]
        is True
    )

    clock["now"] += MEMBER_SESSION_TTL + 1  # 越过 24h 有效期
    assert (
        client.get("/api/member/status", headers={"X-Member-Token": token}).json()[
            "is_member"
        ]
        is False
    )


def test_member_key_works_after_session_expiry(client: TestClient, monkeypatch) -> None:
    """会员身份收回后: 重新提交密钥可再次解锁 (新会话, 旧 token 不复活)."""
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(auth_mod, "_now", lambda: clock["now"])

    old_token = client.post("/api/member", json={"key": MEMBER_KEY}).json()["token"]
    clock["now"] += MEMBER_SESSION_TTL + 1  # 旧会话过期
    resp = client.post("/api/member", json={"key": MEMBER_KEY})
    assert resp.status_code == 200
    assert resp.json()["is_member"] is True
    new_token = resp.json()["token"]
    assert new_token != old_token  # 新会话签发新 token
    # 旧 token 已被收回, 新 token 有效
    assert (
        client.get("/api/member/status", headers={"X-Member-Token": old_token}).json()[
            "is_member"
        ]
        is False
    )
    assert (
        client.get("/api/member/status", headers={"X-Member-Token": new_token}).json()[
            "is_member"
        ]
        is True
    )


def test_member_non_ascii_key_rejected_without_500(client: TestClient) -> None:
    """非 ASCII 错误密钥: 401 明确拒绝 (不能因 compare_digest 异常 500)."""
    resp = client.post("/api/member", json={"key": "密钥-hànzì-😀"})
    assert resp.status_code == 401
    assert "密钥无效" in resp.json()["detail"]
