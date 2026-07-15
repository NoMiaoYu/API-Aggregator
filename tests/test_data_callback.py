"""data 回调 API 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.cache import CacheEntry, DataCache
from app.main import create_app
from app.plugins.config import EndpointSpec, PluginConfig


def _make_plugin(name="fan_weather", token="tok", path="/api/weather"):
    return PluginConfig(
        name=name,
        token=token,
        description="d",
        fields={},
        endpoints=[],
        file_path=Path(__file__),
    ).__class__  # placeholder


def _plugin(name="fan_weather", token="tok", path="/api/weather", protocols=("http", "ws")):
    return PluginConfig(
        name=name,
        token=token,
        description="d",
        fields={},
        endpoints=[EndpointSpec(path=path, protocols=list(protocols))],
        file_path=Path(__file__),
    )


@pytest.fixture
def app_and_client():
    plugin = _plugin()
    app = create_app(cache=DataCache(), plugins=[plugin])
    return app, TestClient(app)


def test_missing_token_returns_401(app_and_client):
    _, client = app_and_client
    resp = client.post(
        "/api/internal/callback/data",
        json={"endpoint": "/api/weather", "data": {"x": 1}},
    )
    assert resp.status_code == 401


def test_wrong_token_returns_401(app_and_client):
    _, client = app_and_client
    resp = client.post(
        "/api/internal/callback/data",
        json={"endpoint": "/api/weather", "data": {"x": 1}},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_unknown_endpoint_returns_404(app_and_client):
    _, client = app_and_client
    resp = client.post(
        "/api/internal/callback/data",
        json={"endpoint": "/api/missing", "data": {"x": 1}},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 404


def test_success_writes_to_cache(app_and_client):
    app, client = app_and_client
    resp = client.post(
        "/api/internal/callback/data",
        json={
            "endpoint": "/api/weather",
            "data": {"temp": 25},
            "md5": "abc",
            "timestamp": 1700000000,
            "source": "fan_weather",
        },
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    entry = app.state.cache.get("/api/weather")
    assert entry is not None
    assert entry.data == {"temp": 25}
    assert entry.md5 == "abc"
    assert entry.timestamp == 1700000000
    assert entry.source == "fan_weather"


def test_success_without_source_uses_plugin_name(app_and_client):
    app, client = app_and_client
    client.post(
        "/api/internal/callback/data",
        json={"endpoint": "/api/weather", "data": {"x": 1}, "md5": "m"},
        headers={"Authorization": "Bearer tok"},
    )
    entry = app.state.cache.get("/api/weather")
    assert entry is not None
    assert entry.source == "fan_weather"


def test_payload_overwrites_previous_data(app_and_client):
    app, client = app_and_client
    for payload in [
        {"endpoint": "/api/weather", "data": {"v": 1}, "md5": "a"},
        {"endpoint": "/api/weather", "data": {"v": 2}, "md5": "b"},
    ]:
        client.post(
            "/api/internal/callback/data",
            json=payload,
            headers={"Authorization": "Bearer tok"},
        )
    entry = app.state.cache.get("/api/weather")
    assert entry is not None
    assert entry.data == {"v": 2}
    assert entry.md5 == "b"


def test_invalid_payload_missing_data_returns_422(app_and_client):
    _, client = app_and_client
    resp = client.post(
        "/api/internal/callback/data",
        json={"endpoint": "/api/weather"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 422


def test_invalid_payload_missing_endpoint_returns_422(app_and_client):
    _, client = app_and_client
    resp = client.post(
        "/api/internal/callback/data",
        json={"data": {"x": 1}},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 422


def test_broadcaster_is_invoked_on_success():
    """如果 app.state.broadcaster 存在，应被调用。"""
    plugin = PluginConfig(
        name="n",
        token="t",
        description="d",
        fields={},
        endpoints=[EndpointSpec(path="/api/x", protocols=["http"])],
        file_path=Path(__file__),
    )
    app = create_app(cache=DataCache(), plugins=[plugin])

    captured = {}

    class FakeBroadcaster:
        async def broadcast_update(self, **kwargs):
            captured.update(kwargs)

    app.state.broadcaster = FakeBroadcaster()

    client = TestClient(app)
    resp = client.post(
        "/api/internal/callback/data",
        json={
            "endpoint": "/api/x",
            "data": {"v": 1},
            "md5": "m",
            "timestamp": 123,
            "source": "n",
        },
        headers={"Authorization": "Bearer t"},
    )
    assert resp.status_code == 200
    assert captured == {
        "endpoint": "/api/x",
        "source": "n",
        "data": {"v": 1},
        "md5": "m",
        "timestamp": 123,
    }
