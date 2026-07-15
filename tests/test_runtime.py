"""``app.plugins._runtime`` 共享 helper 单元测试。

覆盖：
- ``path_to_source``：路径 → source 字符串
- ``safe_push``：成功路径 + push 失败上报 + report_error 失败吞掉
- ``safe_report_error``：成功 + 静默
- ``supervise_plugin``：正常完成 + CancelledError 取消 + owns_client 关闭
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.plugins._runtime import (
    path_to_source,
    safe_push,
    safe_report_error,
    supervise_plugin,
)
from app.plugins.client import compute_md5


# ------------------------- path_to_source -------------------------


def test_path_to_source_strips_leading_slash():
    assert path_to_source("/cenc") == "cenc"


def test_path_to_source_strips_api_prefix():
    assert path_to_source("/api/cwa-eew") == "cwa-eew"
    assert path_to_source("/api/hko-felt") == "hko-felt"


def test_path_to_source_passes_through_no_api():
    """无 /api/ 前缀时不修改。"""
    assert path_to_source("/foo") == "foo"


def test_path_to_source_strips_api_prefix_without_leading_slash():
    """无前导 / 但有 api/ 前缀时也去掉。"""
    assert path_to_source("api/cenc") == "cenc"


# ------------------------- FakeClient -------------------------


class _FakeClient:
    def __init__(
        self,
        *,
        push_raises: Exception | None = None,
        report_raises: Exception | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self._push_raises = push_raises
        self._report_raises = report_raises
        self.closed = False

    async def push(
        self,
        endpoint: str,
        data: Any,
        source: str | None = None,
        md5: str | None = None,
    ) -> dict:
        self.calls.append(
            {
                "endpoint": endpoint,
                "data": data,
                "source": source,
                "md5": md5,
            }
        )
        if self._push_raises is not None:
            raise self._push_raises
        return {"ok": True}

    async def report_error(
        self,
        endpoint: str,
        error_type: str,
        message: str,
        details: Any | None = None,
    ) -> dict:
        if self._report_raises is not None:
            raise self._report_raises
        self.errors.append(
            {
                "endpoint": endpoint,
                "error_type": error_type,
                "message": message,
                "details": details,
            }
        )
        return {"ok": True}

    async def close(self) -> None:
        self.closed = True


# ------------------------- safe_push -------------------------


@pytest.mark.asyncio
async def test_safe_push_calls_push_with_source_and_md5():
    client = _FakeClient()
    data = {"lat": 1, "mag": 5}
    await safe_push(client, "/api/hko-quake", data)  # type: ignore[arg-type]
    assert len(client.calls) == 1
    call = client.calls[0]
    assert call["endpoint"] == "/api/hko-quake"
    assert call["data"] == data
    assert call["source"] == "hko-quake"
    assert call["md5"] == compute_md5(data)


@pytest.mark.asyncio
async def test_safe_push_uses_provided_md5():
    client = _FakeClient()
    await safe_push(client, "/api/cenc", {"x": 1}, md5="custom-hash")  # type: ignore[arg-type]
    assert client.calls[0]["md5"] == "custom-hash"


@pytest.mark.asyncio
async def test_safe_push_reports_pushed_failed_on_push_error():
    client = _FakeClient(push_raises=RuntimeError("network down"))
    await safe_push(client, "/api/cwa", {"x": 1})  # type: ignore[arg-type]
    assert len(client.errors) == 1
    err = client.errors[0]
    assert err["endpoint"] == "/api/cwa"
    assert err["error_type"] == "PUSH_FAILED"
    assert "network down" in err["message"]


@pytest.mark.asyncio
async def test_safe_push_swallows_report_error_failures():
    client = _FakeClient(
        push_raises=RuntimeError("primary fail"),
        report_raises=RuntimeError("report also failed"),
    )
    # 不应抛
    await safe_push(client, "/api/hko", {"x": 1})  # type: ignore[arg-type]


# ------------------------- safe_report_error -------------------------


@pytest.mark.asyncio
async def test_safe_report_error_calls_client():
    client = _FakeClient()
    await safe_report_error(
        client, "/api/cwa", "FETCH_FAILED", "boom", {"k": 1}  # type: ignore[arg-type]
    )
    assert client.errors == [
        {
            "endpoint": "/api/cwa",
            "error_type": "FETCH_FAILED",
            "message": "boom",
            "details": {"k": 1},
        }
    ]


@pytest.mark.asyncio
async def test_safe_report_error_silent_on_failure():
    client = _FakeClient(report_raises=RuntimeError("report broken"))
    # 不应抛
    await safe_report_error(client, "/api/cwa", "X", "y")  # type: ignore[arg-type]


# ------------------------- supervise_plugin -------------------------


@pytest.mark.asyncio
async def test_supervise_plugin_runs_to_completion():
    async def task_a() -> None:
        await asyncio.sleep(0.01)

    async def task_b() -> None:
        await asyncio.sleep(0.01)

    stop = asyncio.Event()
    tasks = [asyncio.create_task(task_a()), asyncio.create_task(task_b())]
    await supervise_plugin(tasks, stop)
    for t in tasks:
        assert t.done()


@pytest.mark.asyncio
async def test_supervise_plugin_cancels_tasks_on_cancellation():
    started = asyncio.Event()

    async def long_task() -> None:
        started.set()
        await asyncio.sleep(10)

    stop = asyncio.Event()
    tasks = [asyncio.create_task(long_task(), name="t1")]
    runner = asyncio.create_task(supervise_plugin(tasks, stop))
    await started.wait()
    runner.cancel()
    try:
        await runner
    except asyncio.CancelledError:
        pass

    # 所有 task 应已被取消
    for t in tasks:
        assert t.cancelled() or t.done()
    # stop_event 已被 set
    assert stop.is_set()


@pytest.mark.asyncio
async def test_supervise_plugin_closes_client_when_owns():
    client = _FakeClient()

    async def noop() -> None:
        await asyncio.sleep(0.01)

    stop = asyncio.Event()
    tasks = [asyncio.create_task(noop())]
    await supervise_plugin(tasks, stop, client, owns_client=True)  # type: ignore[arg-type]
    assert client.closed is True


@pytest.mark.asyncio
async def test_supervise_plugin_does_not_close_when_not_owns():
    client = _FakeClient()

    async def noop() -> None:
        await asyncio.sleep(0.01)

    stop = asyncio.Event()
    tasks = [asyncio.create_task(noop())]
    await supervise_plugin(tasks, stop, client, owns_client=False)  # type: ignore[arg-type]
    assert client.closed is False


@pytest.mark.asyncio
async def test_supervise_plugin_handles_task_exception():
    """子 task 抛异常被 gather(return_exceptions=True) 吞下，supervisor 正常退出。"""

    async def bad() -> None:
        raise ValueError("oops")

    stop = asyncio.Event()
    tasks = [asyncio.create_task(bad())]
    # 不应向上抛
    await supervise_plugin(tasks, stop)
    for t in tasks:
        assert t.done()
