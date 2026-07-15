"""内部回调 API：插件把数据 / 错误推回主程序。"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.core.cache import CacheEntry

router = APIRouter(prefix="/api/internal/callback")
logger = logging.getLogger(__name__)


def _check_token(request: Request, expected: str) -> None:
    """校验 Authorization Bearer 头是否等于 expected。"""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth[len("Bearer "):].strip()
    if token != expected:
        raise HTTPException(status_code=401, detail="invalid token")


def _find_plugin_by_endpoint(request: Request, endpoint: str):
    """根据 endpoint 路径反查所属 plugin。"""
    for plugin in request.app.state.plugins:
        if plugin.find_endpoint(endpoint) is not None:
            return plugin
    return None


@router.post("/data")
async def callback_data(request: Request):
    """插件推送最新数据。"""
    body = await request.json()
    endpoint = body.get("endpoint")
    data = body.get("data")
    md5 = body.get("md5")
    timestamp = body.get("timestamp")
    source = body.get("source")

    if not isinstance(endpoint, str) or not isinstance(data, (dict, list)):
        raise HTTPException(status_code=422, detail="endpoint 与 data 必填")

    plugin = _find_plugin_by_endpoint(request, endpoint)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"endpoint {endpoint} 未注册")

    _check_token(request, plugin.token)

    # timestamp 缺省补 0；source 缺省用 plugin.source
    entry = CacheEntry(
        data=data,
        md5=md5 or "",
        timestamp=int(timestamp) if timestamp is not None else 0,
        source=source or plugin.source,
    )
    request.app.state.cache.set(endpoint, entry)

    # 广播（如果 broadcaster 已挂载）
    broadcaster = getattr(request.app.state, "broadcaster", None)
    if broadcaster is not None:
        await broadcaster.broadcast_update(
            endpoint=endpoint,
            source=entry.source,
            data=entry.data,
            md5=entry.md5,
            timestamp=entry.timestamp,
        )

    return {"ok": True}


@router.post("/error")
async def callback_error(request: Request):
    """插件上报错误。"""
    body = await request.json()
    endpoint = body.get("endpoint")
    error_type = body.get("error_type")
    message = body.get("message")
    details = body.get("details", {})
    timestamp = body.get("timestamp")

    if not isinstance(endpoint, str) or not isinstance(error_type, str):
        raise HTTPException(status_code=422, detail="endpoint 与 error_type 必填")

    plugin = _find_plugin_by_endpoint(request, endpoint)
    if plugin is None:
        raise HTTPException(status_code=404, detail=f"endpoint {endpoint} 未注册")

    _check_token(request, plugin.token)

    # 错误日志：每行一条 JSON
    log_dir: Path | None = getattr(request.app.state, "log_dir", None)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "error_type": error_type,
            "message": message,
            "endpoint": endpoint,
            "plugin": plugin.name,
            "datetime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timestamp": timestamp,
            "details": details,
        }
        with (log_dir / "errors.log").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {"ok": True}
