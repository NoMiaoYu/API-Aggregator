"""/ws/all WebSocket 路由测试。"""

from __future__ import annotations

import json
from functools import partial
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.cache import CacheEntry, DataCache
from app.main import create_app
from app.plugins.config import EndpointSpec, PluginConfig


def _plugin(name, token, eps):
    return PluginConfig(
        name=name,
        token=token,
        description="d",
        fields={},
        endpoints=eps,
        file_path=Path(__file__),
    )


def _broadcast(ws, app, **kwargs):
    bc = app.state.broadcaster
    return ws.portal.call(partial(bc.broadcast_update, **kwargs))


def test_initial_all_contains_only_non_excluded_endpoints_with_cache():
    plugin = _plugin("fan_weather", "tok", [
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
        EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
    ])
    app = create_app(cache=DataCache(), plugins=[plugin])
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"temp": 25}, md5="abc", timestamp=1700000000, source="fan_weather"),
    )
    app.state.cache.set(
        "/api/weather_alert",
        CacheEntry(data={"alert": "storm"}, md5="def", timestamp=1700000001, source="fan_weather"),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/all") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "initial_all"
        assert msg == {
            "type": "initial_all",
            "fan_weather": {
                "/api/weather": {
                    "Data": {"temp": 25},
                    "md5": "abc",
                    "timestamp": 1700000000,
                },
            },
        }


def test_initial_all_skips_endpoints_with_empty_cache():
    plugin = _plugin("fan_weather", "tok", [
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
        EndpointSpec(path="/api/weather_extra", protocols=["http", "ws"]),
    ])
    app = create_app(cache=DataCache(), plugins=[plugin])
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"x": 1}, md5="m", timestamp=1, source="fan_weather"),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/all") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "initial_all"
        # /api/weather_extra 没缓存 → 不应出现
        assert msg == {
            "type": "initial_all",
            "fan_weather": {
                "/api/weather": {"Data": {"x": 1}, "md5": "m", "timestamp": 1},
            },
        }


def test_initial_all_empty_when_no_data():
    plugin = _plugin("fan_weather", "tok", [
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
    ])
    app = create_app(cache=DataCache(), plugins=[plugin])
    client = TestClient(app)
    with client.websocket_connect("/ws/all") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "initial_all"
        # 没有 source 字段（plugin 下没数据）
        assert "fan_weather" not in msg


def test_initial_all_aggregates_multiple_plugins():
    p1 = _plugin("fan_weather", "t1", [
        EndpointSpec(path="/api/weather", protocols=["ws"]),
    ])
    p2 = _plugin("other_src", "t2", [
        EndpointSpec(path="/api/other", protocols=["ws"]),
    ])
    app = create_app(cache=DataCache(), plugins=[p1, p2])
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"a": 1}, md5="m1", timestamp=1, source="fan_weather"),
    )
    app.state.cache.set(
        "/api/other",
        CacheEntry(data={"b": 2}, md5="m2", timestamp=2, source="other_src"),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/all") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "initial_all"
        assert "fan_weather" in msg
        assert "other_src" in msg
        assert msg["fan_weather"]["/api/weather"]["Data"] == {"a": 1}
        assert msg["other_src"]["/api/other"]["Data"] == {"b": 2}


def test_update_message_includes_endpoint_and_timestamp():
    plugin = _plugin("fan_weather", "tok", [
        EndpointSpec(path="/api/weather", protocols=["ws"]),
    ])
    app = create_app(cache=DataCache(), plugins=[plugin])
    client = TestClient(app)
    with client.websocket_connect("/ws/all") as ws:
        ws.receive_json()  # initial_all
        _broadcast(
            ws, app,
            endpoint="/api/weather",
            source="fan_weather",
            data={"v": 1},
            md5="m",
            timestamp=1234,
        )
        msg = ws.receive_json()
        assert msg == {
            "type": "update",
            "source": "fan_weather",
            "endpoint": "/api/weather",
            "Data": {"v": 1},
            "md5": "m",
            "timestamp": 1234,
        }


def test_query_response_returns_initial_all_shape():
    plugin = _plugin("fan_weather", "tok", [
        EndpointSpec(path="/api/weather", protocols=["ws"]),
    ])
    app = create_app(cache=DataCache(), plugins=[plugin])
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"x": 1}, md5="m", timestamp=1, source="fan_weather"),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/all") as ws:
        ws.receive_json()  # initial_all
        ws.send_text(json.dumps({"type": "query"}))
        resp = ws.receive_json()
        assert resp["type"] == "query_response"
        assert resp["fan_weather"]["/api/weather"]["Data"] == {"x": 1}
        # query_response 与 initial_all 形状一致
        assert resp["fan_weather"]["/api/weather"]["timestamp"] == 1


def test_excluded_endpoint_still_receives_updates_on_subscribed_client():
    """exclude_from_all=True 的 endpoint 仍可在自己的 /ws/api/... 收到 update。"""
    plugin = _plugin("fan_weather", "tok", [
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
        EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
    ])
    app = create_app(cache=DataCache(), plugins=[plugin])
    client = TestClient(app)
    with client.websocket_connect("/ws/api/weather_alert") as ws:
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


def test_update_for_other_endpoint_does_not_trigger_weather_alert_subscriber():
    plugin = _plugin("fan_weather", "tok", [
        EndpointSpec(path="/api/weather", protocols=["ws"]),
        EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
    ])
    app = create_app(cache=DataCache(), plugins=[plugin])
    client = TestClient(app)

    def _expect_no_message(ws, timeout=0.3):
        import threading

        received = {}

        def runner():
            try:
                received["msg"] = ws.receive()
            except Exception as exc:  # noqa: BLE001
                received["err"] = exc

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if received:
            raise AssertionError(f"expected no message but got: {received}")

    with client.websocket_connect("/ws/api/weather_alert") as ws:
        _broadcast(
            ws, app,
            endpoint="/api/weather",
            source="fan_weather",
            data={"x": 1},
            md5="m",
            timestamp=1,
        )
        _expect_no_message(ws, timeout=0.3)
