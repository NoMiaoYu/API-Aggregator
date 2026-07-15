"""PluginClient 与 compute_md5 单元测试。"""

from __future__ import annotations

import hashlib
import json

import httpx
import pytest
import respx

from app.plugins.client import PluginClient, compute_md5


# ------------------------- compute_md5 -------------------------


def test_compute_md5_is_deterministic_for_same_dict():
    a = compute_md5({"b": 1, "a": 2})
    b = compute_md5({"a": 2, "b": 1})
    assert a == b


def test_compute_md5_changes_when_value_changes():
    assert compute_md5({"x": 1}) != compute_md5({"x": 2})


def test_compute_md5_handles_chinese_chars():
    expected = hashlib.md5(
        json.dumps({"city": "上海"}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    assert compute_md5({"city": "上海"}) == expected


# ------------------------- PluginClient.push -------------------------


@pytest.mark.asyncio
async def test_push_sends_data_payload_with_bearer_token():
    with respx.mock(base_url="http://cb") as router:
        route = router.post("/api/internal/callback/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = PluginClient("http://cb", "tok-1")
        await client.push("/api/weather", {"temp": 25})
    assert route.called
    sent = route.calls.last.request
    body = json.loads(sent.content)
    assert body["endpoint"] == "/api/weather"
    assert body["data"] == {"temp": 25}
    assert body["md5"] == compute_md5({"temp": 25})
    assert body["source"] == "weather"
    assert "timestamp" in body
    assert sent.headers["authorization"] == "Bearer tok-1"
    await client.close()


@pytest.mark.asyncio
async def test_push_accepts_explicit_source_and_md5():
    with respx.mock(base_url="http://cb") as router:
        route = router.post("/api/internal/callback/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = PluginClient("http://cb", "tok")
        await client.push("/api/foo", {"x": 1}, source="custom", md5="deadbeef")
    body = json.loads(route.calls.last.request.content)
    assert body["source"] == "custom"
    assert body["md5"] == "deadbeef"
    await client.close()


@pytest.mark.asyncio
async def test_push_derives_source_from_endpoint():
    with respx.mock(base_url="http://cb") as router:
        route = router.post("/api/internal/callback/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = PluginClient("http://cb", "tok")
        await client.push("/api/weather_alert", {"y": 1})
    body = json.loads(route.calls.last.request.content)
    assert body["source"] == "weather_alert"
    await client.close()


@pytest.mark.asyncio
async def test_push_returns_response_json():
    with respx.mock(base_url="http://cb") as router:
        router.post("/api/internal/callback/data").mock(
            return_value=httpx.Response(200, json={"ok": True, "x": 1})
        )
        client = PluginClient("http://cb", "tok")
        result = await client.push("/api/x", {})
    assert result == {"ok": True, "x": 1}
    await client.close()


@pytest.mark.asyncio
async def test_push_raises_on_http_error():
    with respx.mock(base_url="http://cb") as router:
        router.post("/api/internal/callback/data").mock(
            return_value=httpx.Response(401, json={"detail": "bad token"})
        )
        client = PluginClient("http://cb", "tok")
        with pytest.raises(httpx.HTTPStatusError):
            await client.push("/api/x", {})
        await client.close()


@pytest.mark.asyncio
async def test_push_strips_trailing_slash_in_callback_url():
    with respx.mock(base_url="http://cb") as router:
        route = router.post("/api/internal/callback/data").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = PluginClient("http://cb/", "tok")
        await client.push("/api/x", {})
    assert route.called
    await client.close()


# ------------------------- PluginClient.report_error -------------------------


@pytest.mark.asyncio
async def test_report_error_sends_expected_payload():
    with respx.mock(base_url="http://cb") as router:
        route = router.post("/api/internal/callback/error").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = PluginClient("http://cb", "tok")
        await client.report_error(
            "/api/foo", "UPSTREAM_TIMEOUT", "timeout", {"host": "x"}
        )
    sent = route.calls.last.request
    body = json.loads(sent.content)
    assert body == {
        "endpoint": "/api/foo",
        "error_type": "UPSTREAM_TIMEOUT",
        "message": "timeout",
        "details": {"host": "x"},
        "timestamp": body["timestamp"],
    }
    assert sent.headers["authorization"] == "Bearer tok"
    await client.close()


@pytest.mark.asyncio
async def test_report_error_defaults_details_to_empty_dict():
    with respx.mock(base_url="http://cb") as router:
        route = router.post("/api/internal/callback/error").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )
        client = PluginClient("http://cb", "tok")
        await client.report_error("/api/x", "TYPE", "msg")
    body = json.loads(route.calls.last.request.content)
    assert body["details"] == {}
    await client.close()


# ------------------------- close -------------------------


@pytest.mark.asyncio
async def test_close_is_idempotent():
    client = PluginClient("http://cb", "tok")
    await client.close()
    await client.close()  # 二次关闭不应抛
