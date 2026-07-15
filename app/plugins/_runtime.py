"""插件运行时共享工具。

集中 ``safe_push`` / ``safe_report_error`` / ``supervise_plugin`` 等样板，
消除各插件的重复逻辑。所有 ``except Exception`` 均带
``# pylint: disable=broad-exception-caught``（网络/序列化场景必须宽泛捕获）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Iterable

from app.plugins.client import PluginClient, compute_md5


def path_to_source(path: str) -> str:
    """endpoint path → source 字段：去前导 ``/`` 再去 ``api/`` 前缀。

    例：``/api/cwa-eew`` → ``cwa-eew``，``/api/my-source`` → ``my-source``。
    """
    stripped = path.lstrip("/")
    if stripped.startswith("api/"):
        stripped = stripped[len("api/"):]
    return stripped


async def safe_push(
    client: PluginClient,
    endpoint: str,
    data: Any,
    *,
    md5: str | None = None,
) -> None:
    """把一条上游数据推回主程序。push 失败上报 ``PUSH_FAILED``。

    report_error 自身失败也吞掉，避免阻塞监听循环。
    """
    if md5 is None:
        md5 = compute_md5(data)
    try:
        await client.push(
            endpoint, data, source=path_to_source(endpoint), md5=md5
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        await safe_report_error(
            client,
            endpoint,
            "PUSH_FAILED",
            str(exc),
            {"data_type": type(data).__name__},
        )


async def safe_report_error(
    client: PluginClient,
    endpoint: str,
    error_type: str,
    message: str,
    details: Any = None,
) -> None:
    """上报错误。report_error 自身失败静默（不阻塞循环）。"""
    try:
        await client.report_error(endpoint, error_type, message, details)
    except Exception:  # pylint: disable=broad-exception-caught
        pass


async def supervise_plugin(
    tasks: Iterable[asyncio.Task[None]],
    stop_event: asyncio.Event,
    client: PluginClient | None = None,
    *,
    owns_client: bool = False,
) -> None:
    """统一管理一组异步 task 的生命周期。

    - ``asyncio.gather(..., return_exceptions=True)`` 等待所有 task；
    - ``finally`` 块统一 ``set stop_event`` + ``cancel`` 全部 task +
      按需 ``close`` client；不管正常返回还是 ``CancelledError`` 都走这条路径。
    """
    task_list = list(tasks)
    try:
        await asyncio.gather(*task_list, return_exceptions=True)
    finally:
        stop_event.set()
        for t in task_list:
            t.cancel()
        if owns_client and client is not None:
            await client.close()
