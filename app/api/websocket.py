"""WebSocket 路由 + 广播器。

路由：
- GET /ws/all                  -> 聚合（_handle_all）
- GET /ws/api/{path:path}      -> 单端点（_handle_single）

约定：所有 WS 帧里 `Data` 字段名用大写 D，`timestamp` 用 Unix 秒。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, FastAPI, WebSocket, WebSocketDisconnect, status

from app import __version__
from app.core.cache import CacheEntry

logger = logging.getLogger(__name__)

router = APIRouter()
ALL_KEY = "__ALL__"  # 在 _subscribers 字典里表示 /all 的订阅集合

HEARTBEAT_INTERVAL = 30.0


# ------------------------- Broadcaster -------------------------


class _WSBroadcaster:
    """维护 endpoint -> set[WebSocket] 的订阅关系，broadcast 加锁。"""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, key: str, ws: WebSocket) -> None:
        """注册一个订阅者。"""
        async with self._lock:
            self._subscribers.setdefault(key, set()).add(ws)

    async def unsubscribe(self, key: str, ws: WebSocket) -> None:
        """移除订阅者。空集合会自动清理。"""
        async with self._lock:
            subs = self._subscribers.get(key)
            if subs is not None:
                subs.discard(ws)
                if not subs:
                    self._subscribers.pop(key, None)

    def _snapshot_subs(self) -> dict[str, set[WebSocket]]:
        """返回订阅者映射的浅拷贝。"""
        return {k: set(v) for k, v in self._subscribers.items()}

    async def broadcast_update(
        self,
        endpoint: str,
        source: str,
        data: Any,
        md5: str,
        timestamp: int,
    ) -> None:
        """广播一条 update：endpoint 自身订阅者 + /all 订阅者。"""
        frame = {
            "type": "update",
            "source": source,
            "endpoint": endpoint,
            "Data": data,
            "md5": md5,
            "timestamp": timestamp,
        }
        text = json.dumps(frame, ensure_ascii=False)
        for key in (endpoint, ALL_KEY):
            subs = self._snapshot_subs().get(key, set())
            for ws in list(subs):
                try:
                    await ws.send_text(text)
                except (RuntimeError, ConnectionError, OSError) as exc:
                    # 单个客户端断开不应阻塞其他客户端
                    logger.debug("WS send failed for %s: %s", key, exc)


def get_broadcaster(app: FastAPI) -> _WSBroadcaster:
    """从 app.state 懒加载 broadcaster。"""
    bc = getattr(app.state, "broadcaster", None)
    if bc is None:
        bc = _WSBroadcaster()
        app.state.broadcaster = bc
    return bc


# ------------------------- helpers -------------------------


def _find_endpoint_for_ws(app: FastAPI, path: str):
    for plugin in app.state.plugins:
        spec = plugin.find_endpoint(path)
        if spec is not None:
            return plugin, spec
    return None, None


def _build_initial_all(app: FastAPI) -> dict[str, Any]:
    """构造 initial_all 帧：两级结构 {source: {endpoint: entry}}。"""
    out: dict[str, Any] = {"type": "initial_all"}
    for plugin in app.state.plugins:
        per_source: dict[str, Any] = {}
        for ep in plugin.endpoints:
            if ep.exclude_from_all:
                continue
            entry = app.state.cache.get(ep.path)
            if entry is None:
                continue
            per_source[ep.path] = {
                "Data": entry.data,
                "md5": entry.md5,
                "timestamp": entry.timestamp,
            }
        if per_source:
            out[plugin.source] = per_source
    return out


def _build_snapshot(entry: CacheEntry | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "type": "snapshot",
        "Data": entry.data,
        "md5": entry.md5,
        "timestamp": entry.timestamp,
    }


# ------------------------- Handlers -------------------------


async def _handle_single(ws: WebSocket, endpoint: str, app: FastAPI) -> None:
    await ws.accept()
    bc = get_broadcaster(app)
    await bc.subscribe(endpoint, ws)
    try:
        entry = app.state.cache.get(endpoint)
        snap = _build_snapshot(entry)
        if snap is not None:
            await ws.send_text(json.dumps(snap, ensure_ascii=False))

        while True:
            try:
                raw = await ws.receive_text()
            except WebSocketDisconnect:
                return
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict) and msg.get("type") == "query":
                cur = app.state.cache.get(endpoint)
                snap = _build_snapshot(cur)
                if snap is not None:
                    await ws.send_text(json.dumps(snap, ensure_ascii=False))
    finally:
        await bc.unsubscribe(endpoint, ws)


async def _handle_all(ws: WebSocket, app: FastAPI) -> None:
    await ws.accept()
    bc = get_broadcaster(app)
    await bc.subscribe(ALL_KEY, ws)
    try:
        # 1) 立即发 initial_all
        await ws.send_text(json.dumps(_build_initial_all(app), ensure_ascii=False))

        # 2) 启 heartbeat 协程
        async def _heartbeat():
            try:
                while True:
                    await asyncio.sleep(HEARTBEAT_INTERVAL)
                    frame = {
                        "type": "heartbeat",
                        "ver": __version__,
                        "id": str(uuid.uuid4()),
                        "timestamp": int(time.time()),
                    }
                    await ws.send_text(json.dumps(frame, ensure_ascii=False))
            except (WebSocketDisconnect, RuntimeError, ConnectionError, OSError):
                # 客户端断开或连接异常：结束心跳
                return

        hb_task = asyncio.create_task(_heartbeat())

        try:
            while True:
                try:
                    raw = await ws.receive_text()
                except WebSocketDisconnect:
                    return
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "query":
                    resp = _build_initial_all(app)
                    resp["type"] = "query_response"
                    await ws.send_text(json.dumps(resp, ensure_ascii=False))
        finally:
            hb_task.cancel()
    finally:
        await bc.unsubscribe(ALL_KEY, ws)


# ------------------------- Routes -------------------------


@router.websocket("/ws/all")
async def ws_all(websocket: WebSocket):
    """聚合 WebSocket 入口。"""
    await _handle_all(websocket, websocket.app)


@router.websocket("/ws/api/{path:path}")
async def ws_api(websocket: WebSocket, path: str):
    """单端点 WebSocket 入口。"""
    endpoint = f"/api/{path}"
    app = websocket.app
    plugin, spec = _find_endpoint_for_ws(app, endpoint)
    if plugin is None or spec is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="端点不存在")
        return
    if "ws" not in spec.protocols:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="端点不支持 WebSocket")
        return
    await _handle_single(websocket, endpoint, app)
