"""文档路由 /doc/ws-api/... 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.cache import DataCache
from app.docs_gen.generator import DocsGenerator
from app.main import create_app
from app.plugins.config import EndpointSpec, PluginConfig


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    out = tmp_path / "docs"
    plugin = PluginConfig(
        name="fan_weather",
        token="tok",
        description="d",
        fields={},
        endpoints=[EndpointSpec(path="/api/weather", protocols=["http", "ws"])],
        file_path=Path(__file__),
    )
    DocsGenerator([plugin], version="0.1.0", output_dir=out).generate()
    return out


@pytest.fixture
def app_and_client(docs_dir: Path):
    plugin = PluginConfig(
        name="fan_weather",
        token="tok",
        description="d",
        fields={},
        endpoints=[EndpointSpec(path="/api/weather", protocols=["http", "ws"])],
        file_path=Path(__file__),
    )
    app = create_app(cache=DataCache(), plugins=[plugin], docs_dir=docs_dir)
    return app, TestClient(app)


def test_index_returns_html(app_and_client):
    _, client = app_and_client
    resp = client.get("/doc/ws-api/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "/api/weather" in resp.text


def test_page_with_html_suffix(app_and_client):
    _, client = app_and_client
    resp = client.get("/doc/ws-api/index.html")
    assert resp.status_code == 200
    assert "/api/weather" in resp.text


def test_page_without_html_suffix(app_and_client):
    _, client = app_and_client
    resp = client.get("/doc/ws-api/all")
    assert resp.status_code == 200
    assert "包含在 /all" in resp.text or "被排除" in resp.text


def test_per_endpoint_page(app_and_client):
    _, client = app_and_client
    resp = client.get("/doc/ws-api/weather")
    assert resp.status_code == 200
    assert "/api/weather" in resp.text


def test_per_endpoint_page_with_html_suffix(app_and_client):
    _, client = app_and_client
    resp = client.get("/doc/ws-api/weather.html")
    assert resp.status_code == 200


def test_unknown_page_returns_404(app_and_client):
    _, client = app_and_client
    resp = client.get("/doc/ws-api/nonexistent")
    assert resp.status_code == 404


def test_path_traversal_blocked_slash(app_and_client):
    _, client = app_and_client
    resp = client.get("/doc/ws-api/../etc/passwd")
    # FastAPI 会先处理 ..，但我们内部仍要挡住
    assert resp.status_code in (400, 404, 307, 308)


def test_path_traversal_blocked_explicit_dotdot(app_and_client):
    _, client = app_and_client
    # 直接访问 .. 开头
    resp = client.get("/doc/ws-api/..passwd")
    assert resp.status_code == 404


def test_dotfile_blocked(app_and_client):
    _, client = app_and_client
    resp = client.get("/doc/ws-api/.hidden")
    assert resp.status_code == 404


def test_docs_dir_not_configured_returns_503():
    app = create_app(cache=DataCache(), plugins=[])
    client = TestClient(app)
    resp = client.get("/doc/ws-api/")
    assert resp.status_code == 503


def test_no_index_file_returns_404(tmp_path: Path):
    app = create_app(cache=DataCache(), plugins=[], docs_dir=tmp_path)
    client = TestClient(app)
    resp = client.get("/doc/ws-api/")
    assert resp.status_code == 404
