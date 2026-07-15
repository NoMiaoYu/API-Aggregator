"""对外 HTTP API：GET /api/{path} 返回最新数据。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api")


@router.get("/{path:path}")
async def get_endpoint(path: str, request: Request):
    """返回某 endpoint 的最新数据快照。"""
    # 1) /api/all 必须 405（仅支持 WebSocket）
    if path == "all":
        raise HTTPException(
            status_code=405, detail="/api/all 仅支持 WebSocket，请使用 /ws/all"
        )

    # 2) 查 endpoint 是否注册
    endpoint_path = f"/api/{path}"
    plugin = None
    spec = None
    for p in request.app.state.plugins:
        s = p.find_endpoint(endpoint_path)
        if s is not None:
            plugin = p
            spec = s
            break
    if plugin is None or spec is None:
        raise HTTPException(status_code=404, detail=f"endpoint {endpoint_path} 未注册")

    # 3) 协议不匹配 → 405
    if "http" not in spec.protocols:
        raise HTTPException(
            status_code=405,
            detail=f"endpoint {endpoint_path} 不支持 HTTP，仅支持 {spec.protocols}",
        )

    # 4) 缓存无数据 → 503
    entry = request.app.state.cache.get(endpoint_path)
    if entry is None:
        raise HTTPException(
            status_code=503, detail=f"endpoint {endpoint_path} 暂未收到数据"
        )

    # 5) 正常返回
    return {
        "Data": entry.data,
        "md5": entry.md5,
        "timestamp": entry.timestamp,
    }
