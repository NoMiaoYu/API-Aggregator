"""多端点插件端到端集成测试。

模拟一个真实的插件（用 PluginClient + asyncio 协程直接驱动，不开子进程），
验证主程序侧能正确接收 fan-out 数据并按 endpoint 维度缓存 / 路由 / 广播。
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.cache import CacheEntry, DataCache
from app.main import create_app
from app.plugins.client import PluginClient, compute_md5
from app.plugins.config import EndpointSpec, PluginConfig


def _plugin():
    return PluginConfig(
        name="fan_weather",
        token="tok",
        description="d",
        fields={},
        endpoints=[
            EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
            EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
        ],
        file_path=Path(__file__),
    )


def _broadcast(ws, app, **kwargs):
    bc = app.state.broadcaster
    return ws.portal.call(partial(bc.broadcast_update, **kwargs))


@pytest.mark.asyncio
async def test_get_weather_returns_fanout_data():
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    # 模拟插件 fan-out 推送
    PluginClient("http://placeholder", "tok")
    # 直接用 callback API
    with TestClient(app) as tc:
        tc.post(
            "/api/internal/callback/data",
            json={
                "endpoint": "/api/weather",
                "data": {"temp": 25, "humidity": 60, "city": "上海"},
                "md5": compute_md5({"temp": 25, "humidity": 60, "city": "上海"}),
                "timestamp": 1700000000,
                "source": "fan_weather",
            },
            headers={"Authorization": "Bearer tok"},
        )
        resp = tc.get("/api/weather")
        assert resp.status_code == 200
        body = resp.json()
        assert body["Data"] == {"temp": 25, "humidity": 60, "city": "上海"}
        assert body["timestamp"] == 1700000000


def test_get_weather_alert_returns_405_http():
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    app.state.cache.set(
        "/api/weather_alert",
        CacheEntry(data={"a": 1}, md5="m", timestamp=1, source="fan_weather"),
    )
    with TestClient(app) as tc:
        resp = tc.get("/api/weather_alert")
        assert resp.status_code == 405


def test_ws_weather_receives_update():
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/api/weather") as ws:
            _broadcast(
                ws, app,
                endpoint="/api/weather",
                source="fan_weather",
                data={"temp": 25},
                md5="m",
                timestamp=1234,
            )
            msg = ws.receive_json()
            assert msg["endpoint"] == "/api/weather"
            assert msg["Data"] == {"temp": 25}


def test_ws_weather_alert_receives_update():
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/api/weather_alert") as ws:
            _broadcast(
                ws, app,
                endpoint="/api/weather_alert",
                source="fan_weather",
                data={"alert": "storm"},
                md5="m",
                timestamp=42,
            )
            msg = ws.receive_json()
            assert msg["endpoint"] == "/api/weather_alert"
            assert msg["Data"] == {"alert": "storm"}


def test_ws_all_initial_all_excludes_excluded_endpoint():
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    # 模拟插件已经分别推过两个 endpoint
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"temp": 25}, md5="m1", timestamp=1, source="fan_weather"),
    )
    app.state.cache.set(
        "/api/weather_alert",
        CacheEntry(data={"alert": "storm"}, md5="m2", timestamp=2, source="fan_weather"),
    )
    with TestClient(app) as tc:
        with tc.websocket_connect("/ws/all") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "initial_all"
            assert "fan_weather" in msg
            # /api/weather 在 /all 里
            assert "/api/weather" in msg["fan_weather"]
            # /api/weather_alert 不在 /all 里
            assert "/api/weather_alert" not in msg["fan_weather"]


def test_fanout_does_not_overwrite_other_endpoint():
    """同一 plugin 的多 endpoint 推送互不覆盖。"""
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    with TestClient(app) as tc:
        tc.post(
            "/api/internal/callback/data",
            json={
                "endpoint": "/api/weather",
                "data": {"temp": 25},
                "md5": "m1",
                "timestamp": 1,
                "source": "fan_weather",
            },
            headers={"Authorization": "Bearer tok"},
        )
        tc.post(
            "/api/internal/callback/data",
            json={
                "endpoint": "/api/weather_alert",
                "data": {"alert": "storm"},
                "md5": "m2",
                "timestamp": 2,
                "source": "fan_weather",
            },
            headers={"Authorization": "Bearer tok"},
        )
        # 两个 endpoint 都应保留各自数据
        w = app.state.cache.get("/api/weather")
        a = app.state.cache.get("/api/weather_alert")
        assert w is not None and w.data == {"temp": 25}
        assert a is not None and a.data == {"alert": "storm"}
