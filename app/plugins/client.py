"""插件开发 SDK：负责把上游数据 / 错误推回主程序。

在插件子进程里这样用：

    from app.plugins.client import PluginClient

    client = PluginClient(callback_url="http://127.0.0.1:8000", token="...")
    await client.push("/api/weather", {"temp": 25})
    await client.report_error("/api/weather", "UPSTREAM_TIMEOUT", "timeout")
    await client.close()
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

import httpx


def compute_md5(data: Any) -> str:
    """对 data 序列化后做 md5。

    序列化规则：json.dumps(..., sort_keys=True, ensure_ascii=False)
    """
    text = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _derive_source(endpoint: str) -> str:
    """endpoint -> source 推导：去前导 / 再去 api/ 前缀。

    例："/api/weather" -> "weather"，"/api/foo/bar" -> "foo/bar"。
    """
    stripped = endpoint.lstrip("/")
    if stripped.startswith("api/"):
        stripped = stripped[len("api/") :]
    return stripped


class PluginClient:
    """与主程序之间的回调客户端。"""

    def __init__(self, callback_url: str, token: str, timeout: float = 10.0) -> None:
        """构造 SDK 客户端。

        callback_url 末尾的 / 会被自动去掉。token 用于 Bearer 鉴权。
        """
        self._base = callback_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client = httpx.AsyncClient(timeout=timeout)

    async def push(
        self,
        endpoint: str,
        data: Any,
        source: str | None = None,
        md5: str | None = None,
    ) -> dict:
        """把一条数据推回主程序。md5 不传则客户端计算；source 不传则由 endpoint 推导。"""
        payload = {
            "endpoint": endpoint,
            "data": data,
            "md5": md5 if md5 is not None else compute_md5(data),
            "timestamp": int(time.time()),
            "source": source if source is not None else _derive_source(endpoint),
        }
        url = f"{self._base}/api/internal/callback/data"
        resp = await self._client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def report_error(
        self,
        endpoint: str,
        error_type: str,
        message: str,
        details: Any | None = None,
    ) -> dict:
        """上报一条错误事件到主程序。"""
        payload = {
            "endpoint": endpoint,
            "error_type": error_type,
            "message": message,
            "details": details if details is not None else {},
            "timestamp": int(time.time()),
        }
        url = f"{self._base}/api/internal/callback/error"
        resp = await self._client.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """关闭底层 httpx 客户端。"""
        await self._client.aclose()
