"""/api/all 的 HTTP 行为测试：必须 405。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.cache import CacheEntry, DataCache
from app.main import create_app
from app.plugins.config import EndpointSpec, PluginConfig


def _plugin():
    return PluginConfig(
        name="fan_weather",
        token="tok",
        description="d",
        fields={},
        endpoints=[EndpointSpec(path="/api/weather", protocols=["http", "ws"])],
        file_path=Path(__file__),
    )


def test_api_all_get_returns_405_with_websocket_hint():
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    client = TestClient(app)
    resp = client.get("/api/all")
    assert resp.status_code == 405
    assert "WebSocket" in resp.json()["detail"] or "WebSocket" in resp.text


def test_api_all_get_405_even_with_cached_data():
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    app.state.cache.set(
        "/api/weather",
        CacheEntry(data={"x": 1}, md5="m", timestamp=1, source="s"),
    )
    client = TestClient(app)
    resp = client.get("/api/all")
    assert resp.status_code == 405


def test_api_all_get_405_even_without_any_data():
    app = create_app(cache=DataCache(), plugins=[])
    client = TestClient(app)
    resp = client.get("/api/all")
    assert resp.status_code == 405


def test_api_all_post_also_returns_405():
    app = create_app(cache=DataCache(), plugins=[_plugin()])
    client = TestClient(app)
    resp = client.post("/api/all")
    assert resp.status_code == 405
