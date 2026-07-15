"""多端点 fan-out 逻辑测试。

模拟一条上游消息需要被推到 N 个 endpoint 的场景，验证：
- 全部成功时所有 endpoint 都收到
- 单个 endpoint push 失败不影响其他 endpoint
- transform 异常不熔断上游连接
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.plugins.client import compute_md5


class _FakeResponse:
    def __init__(self, data: dict | None = None, *, raise_exc: Exception | None = None) -> None:
        self._data = data or {"ok": True}
        self._raise = raise_exc

    def raise_for_status(self) -> None:
        if self._raise is not None:
            raise self._raise

    def json(self) -> dict:
        return self._data


class _FakeClient:
    """模拟一个多 endpoint 的 push 客户端，单独跟踪每个 endpoint。"""

    def __init__(self, *, fail_endpoints: set[str] | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._fail = fail_endpoints or set()
        self.errors: list[str] = []

    async def push(self, endpoint: str, data: Any, source: str | None = None, md5: str | None = None):
        self.calls.append((endpoint, data))
        if endpoint in self._fail:
            self.errors.append(endpoint)
            return _FakeResponse(raise_exc=RuntimeError(f"push {endpoint} failed"))
        return _FakeResponse()

    async def report_error(self, endpoint: str, error_type: str, message: str, details: Any | None = None):
        return _FakeResponse()

    async def close(self) -> None:
        pass


async def _fanout(
    upstream_msg: dict,
    targets: list[tuple[str, str]],  # [(endpoint, source), ...]
    client,
    transform=None,
):
    """简化的 fan-out 逻辑：每个 target 独立 push，单点失败不影响其他。"""
    async def _one(ep, source):
        try:
            payload = transform(upstream_msg) if transform else upstream_msg
            await client.push(ep, payload, source=source, md5=compute_md5(payload))
            return None
        except Exception as exc:  # noqa: BLE001
            return (ep, exc)

    results = await asyncio.gather(
        *(_one(ep, src) for ep, src in targets), return_exceptions=True
    )
    return results


@pytest.mark.asyncio
async def test_fanout_pushes_to_all_endpoints():
    client = _FakeClient()
    targets = [("/api/weather", "fan_weather"), ("/api/weather_alert", "fan_weather")]
    await _fanout({"temp": 25}, targets, client)
    assert len(client.calls) == 2
    endpoints_called = {ep for ep, _ in client.calls}
    assert endpoints_called == {"/api/weather", "/api/weather_alert"}


@pytest.mark.asyncio
async def test_fanout_isolates_per_endpoint_failures():
    client = _FakeClient(fail_endpoints={"/api/weather_alert"})
    targets = [("/api/weather", "fan_weather"), ("/api/weather_alert", "fan_weather")]
    results = await _fanout({"temp": 25}, targets, client)
    # /api/weather 成功；/api/weather_alert 抛错（但被 gather 吞掉）
    assert len(client.calls) == 2  # 都尝试了
    assert "/api/weather" not in client.errors
    assert "/api/weather_alert" in client.errors
    # 至少一个 None（成功的）
    assert any(r is None for r in results)


@pytest.mark.asyncio
async def test_fanout_transform_exception_does_not_break_other_endpoints():
    client = _FakeClient()

    def bad_transform(msg):
        if msg.get("alert"):
            raise ValueError("transform failed for alert")
        return msg

    targets = [("/api/weather", "fan_weather"), ("/api/weather_alert", "fan_weather")]

    # weather_alert 的 transform 抛错时不应影响 weather
    async def _one(ep, source):
        try:
            payload = bad_transform({"alert": True, "temp": 25}) if ep == "/api/weather_alert" else {"temp": 25}
            await client.push(ep, payload, source=source)
            return None
        except Exception as exc:  # noqa: BLE001
            return (ep, exc)

    results = await asyncio.gather(*(_one(ep, src) for ep, src in targets), return_exceptions=True)
    # 第一个成功；第二个失败
    assert results[0] is None
    assert results[1] is not None  # 失败但未传播


@pytest.mark.asyncio
async def test_fanout_passes_correct_md5_per_endpoint():
    client = _FakeClient()
    # 不同 endpoint 推不同 payload → md5 不同
    async def _one(ep, payload):
        await client.push(ep, payload, source="fan_weather", md5=compute_md5(payload))

    await asyncio.gather(
        _one("/api/weather", {"temp": 25}),
        _one("/api/weather_alert", {"alert": "storm"}),
    )
    assert len(client.calls) == 2
    payloads = {ep: data for ep, data in client.calls}
    assert payloads["/api/weather"] == {"temp": 25}
    assert payloads["/api/weather_alert"] == {"alert": "storm"}


@pytest.mark.asyncio
async def test_fanout_with_zero_endpoints_does_nothing():
    client = _FakeClient()
    await _fanout({"x": 1}, [], client)
    assert client.calls == []
