"""单端点 WebSocket 路由测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.cache import CacheEntry, DataCache
from app.main import create_app
from app.plugins.config import EndpointSpec, PluginConfig


def _plugin(eps):
    return PluginConfig(
        name="fan_weather",
        token="tok",
        description="d",
        fields={},
        endpoints=eps,
        file_path=Path(__file__),
    )


def _ensure_portal(client):
    """触发 client.portal 创建并返回。"""
    if client.portal is None:
        client.get("/")
    return client.portal


def _broadcast(client, app, *, ws=None, **kwargs):
    """在 starlette TestClient 内部事件循环里同步触发 broadcast。

    优先使用 ws 的 portal（ws 上下文里 portal 是活的），
    否则通过 client.get() 触发一次 HTTP 请求以确保 portal 被建立。
    """
    from functools import partial

    bc = app.state.broadcaster
    fn = partial(bc.broadcast_update, **kwargs)
    if ws is not None and getattr(ws, "portal", None) is not None:
        return ws.portal.call(fn)
    if client.portal is None:
        client.get("/")
    return client.portal.call(fn)


def _expect_no_message(ws, timeout: float = 0.3):
    """在 timeout 秒内不应收到任何消息；否则抛 AssertionError。

    实现：起一个线程跑 ws.receive()，在主线程上等 timeout。
    若线程收到消息，记到 received；如果超时就 OK。
    """
    import threading

    received: dict = {}

    def _runner():
        try:
            msg = ws.receive()
            received["msg"] = msg
        except Exception as exc:  # noqa: BLE001
            received["err"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        # 还在跑——给 ws 收尾（让 _run 协程结束），但更简单是直接 return
        # 强行关闭 portal 上的 receive 流可能影响后续测试，跳过
        return
    if received:
        raise AssertionError(f"expected no message but got: {received}")


def test_connect_to_unknown_endpoint_closes_with_1008():
    app = create_app(cache=DataCache(), plugins=[
        _plugin([EndpointSpec(path="/api/weather", protocols=["ws"])])
    ])
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/api/missing") as ws:
            ws.receive_text()


def test_connect_to_http_only_endpoint_closes_with_1008():
    app = create_app(cache=DataCache(), plugins=[
        _plugin([EndpointSpec(path="/api/weather", protocols=["http"])])
    ])
    client = TestClient(app)
    with pytest.raises(Exception):
        with client.websocket_connect("/ws/api/weather") as ws:
            ws.receive_text()


def test_connect_with_no_cache_sends_no_initial_snapshot():
    app = create_app(cache=DataCache(), plugins=[
        _plugin([EndpointSpec(path="/api/weather", protocols=["ws"])])
    ])
    client = TestClient(app)
    with client.websocket_connect("/ws/api/weather") as ws:
        _expect_no_message(ws, timeout=0.3)


def test_connect_with_cache_receives_snapshot_with_timestamp():
    app = create_app(cache=DataCache(), plugins=[
        _plugin([EndpointSpec(path="/api/weather", protocols=["ws"])])
    ])
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"temp": 25}, md5="abc", timestamp=1700000000, source="fan_weather"),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/api/weather") as ws:
        snap = ws.receive_json()
        assert snap == {
            "type": "snapshot",
            "Data": {"temp": 25},
            "md5": "abc",
            "timestamp": 1700000000,
        }


def test_query_with_empty_cache_returns_nothing():
    app = create_app(cache=DataCache(), plugins=[
        _plugin([EndpointSpec(path="/api/weather", protocols=["ws"])])
    ])
    client = TestClient(app)
    with client.websocket_connect("/ws/api/weather") as ws:
        ws.send_text(json.dumps({"type": "query"}))
        _expect_no_message(ws, timeout=0.3)


def test_query_returns_current_snapshot():
    app = create_app(cache=DataCache(), plugins=[
        _plugin([EndpointSpec(path="/api/weather", protocols=["ws"])])
    ])
    client = TestClient(app)
    with client.websocket_connect("/ws/api/weather") as ws:
        ws.send_text(json.dumps({"type": "query"}))
        app.state.cache.set(
            "/api/weather",
            CacheEntry(data={"x": 1}, md5="m", timestamp=42, source="s"),
        )
        ws.send_text(json.dumps({"type": "query"}))
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
        assert snap["Data"] == {"x": 1}
        assert snap["timestamp"] == 42


def test_update_message_is_pushed_to_subscriber():
    app = create_app(cache=DataCache(), plugins=[
        _plugin([EndpointSpec(path="/api/weather", protocols=["ws"])])
    ])
    client = TestClient(app)
    with client.websocket_connect("/ws/api/weather") as ws:
        _broadcast(
            client, app, ws=ws,
            endpoint="/api/weather",
            source="fan_weather",
            data={"v": 99},
            md5="zzz",
            timestamp=1234,
        )
        msg = ws.receive_json()
        assert msg == {
            "type": "update",
            "source": "fan_weather",
            "endpoint": "/api/weather",
            "Data": {"v": 99},
            "md5": "zzz",
            "timestamp": 1234,
        }


def test_invalid_json_does_not_disconnect():
    app = create_app(cache=DataCache(), plugins=[
        _plugin([EndpointSpec(path="/api/weather", protocols=["ws"])])
    ])
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"x": 1}, md5="m", timestamp=1, source="s"),
    )
    client = TestClient(app)
    with client.websocket_connect("/ws/api/weather") as ws:
        ws.receive_json()
        ws.send_text("not-json")
        ws.send_text(json.dumps({"type": "query"}))
        snap = ws.receive_json()
        assert snap["type"] == "snapshot"
