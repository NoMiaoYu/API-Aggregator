"""对外 HTTP /api/{path} 单元测试。"""

from __future__ import annotations

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


@pytest.fixture
def http_only_client():
    plugin = _plugin([EndpointSpec(path="/api/weather", protocols=["http"])])
    app = create_app(cache=DataCache(), plugins=[plugin])
    client = TestClient(app)
    return app, client


@pytest.fixture
def ws_only_client():
    plugin = _plugin([EndpointSpec(path="/api/weather_alert", protocols=["ws"])])
    app = create_app(cache=DataCache(), plugins=[plugin])
    return app, TestClient(app)


@pytest.fixture
def both_client():
    plugin = _plugin([
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
        EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
    ])
    app = create_app(cache=DataCache(), plugins=[plugin])
    return app, TestClient(app)


def test_unknown_endpoint_returns_404(http_only_client):
    _, client = http_only_client
    resp = client.get("/api/unknown")
    assert resp.status_code == 404


def test_empty_cache_returns_503(http_only_client):
    _, client = http_only_client
    resp = client.get("/api/weather")
    assert resp.status_code == 503


def test_http_only_endpoint_returns_data_with_fields(http_only_client):
    app, client = http_only_client
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"temp": 25}, md5="abc", timestamp=1700000000, source="fan_weather"),
    )
    resp = client.get("/api/weather")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"Data": {"temp": 25}, "md5": "abc", "timestamp": 1700000000}


def test_data_field_uses_uppercase_d(http_only_client):
    app, client = http_only_client
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"x": 1}, md5="m", timestamp=1, source="s"),
    )
    body = client.get("/api/weather").json()
    assert "Data" in body
    assert "data" not in body  # 不要小写 data


def test_ws_only_endpoint_returns_405(ws_only_client):
    app, client = ws_only_client
    app.state.cache.set(
        "/api/weather_alert",
        CacheEntry(data={"a": 1}, md5="m", timestamp=1, source="s"),
    )
    resp = client.get("/api/weather_alert")
    assert resp.status_code == 405


def test_mixed_protocols_endpoint_supports_http(both_client):
    app, client = both_client
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"x": 1}, md5="m", timestamp=1, source="fan_weather"),
    )
    resp = client.get("/api/weather")
    assert resp.status_code == 200


def test_response_includes_timestamp_field(both_client):
    app, client = both_client
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"v": 1}, md5="m", timestamp=1234567890, source="s"),
    )
    body = client.get("/api/weather").json()
    assert body["timestamp"] == 1234567890
