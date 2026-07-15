"""error 回调 API 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.cache import DataCache
from app.main import create_app
from app.plugins.config import EndpointSpec, PluginConfig


@pytest.fixture
def app_and_log(tmp_path: Path):
    plugin = PluginConfig(
        name="fan_weather",
        token="tok",
        description="d",
        fields={},
        endpoints=[EndpointSpec(path="/api/weather", protocols=["http"])],
        file_path=Path(__file__),
    )
    log_dir = tmp_path / "logs"
    app = create_app(cache=DataCache(), plugins=[plugin], log_dir=log_dir)
    return app, TestClient(app), log_dir


def test_error_callback_missing_token_returns_401(app_and_log):
    _, client, _ = app_and_log
    resp = client.post(
        "/api/internal/callback/error",
        json={"endpoint": "/api/weather", "error_type": "X", "message": "m"},
    )
    assert resp.status_code == 401


def test_error_callback_wrong_token_returns_401(app_and_log):
    _, client, _ = app_and_log
    resp = client.post(
        "/api/internal/callback/error",
        json={"endpoint": "/api/weather", "error_type": "X", "message": "m"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert resp.status_code == 401


def test_error_callback_unknown_endpoint_returns_404(app_and_log):
    _, client, _ = app_and_log
    resp = client.post(
        "/api/internal/callback/error",
        json={"endpoint": "/api/missing", "error_type": "X", "message": "m"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 404


def test_error_callback_writes_to_log_file(app_and_log):
    _, client, log_dir = app_and_log
    resp = client.post(
        "/api/internal/callback/error",
        json={
            "endpoint": "/api/weather",
            "error_type": "UPSTREAM_TIMEOUT",
            "message": "timeout",
            "details": {"host": "x"},
            "timestamp": 1700000000,
        },
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
    err_file = log_dir / "errors.log"
    assert err_file.exists()
    lines = [l for l in err_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["error_type"] == "UPSTREAM_TIMEOUT"
    assert rec["message"] == "timeout"
    assert rec["endpoint"] == "/api/weather"
    assert rec["plugin"] == "fan_weather"
    assert rec["details"] == {"host": "x"}
    assert rec["timestamp"] == 1700000000
    assert "datetime" in rec


def test_error_callback_missing_error_type_returns_422(app_and_log):
    _, client, _ = app_and_log
    resp = client.post(
        "/api/internal/callback/error",
        json={"endpoint": "/api/weather", "message": "m"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 422


def test_error_callback_no_log_dir_works(app_and_log):
    """log_dir 留空时也不应崩。"""
    app, client, _ = app_and_log
    # 把 log_dir 清空
    app.state.log_dir = None
    resp = client.post(
        "/api/internal/callback/error",
        json={"endpoint": "/api/weather", "error_type": "X", "message": "m"},
        headers={"Authorization": "Bearer tok"},
    )
    assert resp.status_code == 200
