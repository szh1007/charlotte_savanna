"""健康检查接口测试 (Issue 01).

DB/Redis 探活行为通过 mock 验证, 不依赖外部服务.
"""

from unittest import mock

from django.test import TestCase


class HealthViewTests(TestCase):
    """GET /api/charplot/health 探活 MySQL 与 Redis."""

    def test_returns_ok_when_db_and_redis_healthy(self):
        with (
            mock.patch("app.charplot.views_api.connection"),
            mock.patch("django.core.cache.cache.client.get_client") as get_client,
        ):
            get_client.return_value.ping.return_value = True
            resp = self.client.get("/api/charplot/health")

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "charplot-django")
        self.assertEqual(data["db"], "ok")
        self.assertEqual(data["redis"], "ok")
        self.assertIn("time", data)

    def test_returns_degraded_when_redis_down(self):
        with (
            mock.patch("app.charplot.views_api.connection"),
            mock.patch("django.core.cache.cache.client.get_client") as get_client,
        ):
            get_client.return_value.ping.side_effect = ConnectionError("redis down")
            resp = self.client.get("/api/charplot/health")

        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["status"], "degraded")
        self.assertEqual(data["db"], "ok")
        self.assertEqual(data["redis"], "error")

    def test_returns_degraded_when_db_down(self):
        from django.db import DatabaseError

        with (
            mock.patch(
                "app.charplot.views_api.connection.ensure_connection",
                side_effect=DatabaseError("db down"),
            ),
            mock.patch("django.core.cache.cache.client.get_client") as get_client,
        ):
            get_client.return_value.ping.return_value = True
            resp = self.client.get("/api/charplot/health")

        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertEqual(data["db"], "error")
        self.assertEqual(data["redis"], "ok")
