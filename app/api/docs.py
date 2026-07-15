"""文档路由：/doc/ws-api/{page} 渲染本地静态 HTML。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(prefix="/doc/ws-api", include_in_schema=False)


def _resolve_safe(docs_dir: Path, page: str) -> Path:
    """把 page 拼接进 docs_dir，做安全检查后返回绝对路径。

    拒绝以 /、\\ 或 . 开头的 page 名（防路径遍历、点文件）。
    """
    if not page or page.startswith(("/", "\\", ".")):
        raise HTTPException(status_code=404, detail="页面不存在")
    target = (docs_dir / page).resolve()
    base = docs_dir.resolve()
    # 防止 target 跳出 base
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="页面不存在") from exc
    return target


@router.get("/", include_in_schema=False)
async def docs_index(request: Request):
    """返回文档首页。"""
    docs_dir: Path | None = getattr(request.app.state, "docs_dir", None)
    if docs_dir is None:
        raise HTTPException(status_code=503, detail="docs 目录未配置")
    index = docs_dir / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="index.html 未生成")
    return FileResponse(str(index), media_type="text/html")


@router.get("/{page}", include_in_schema=False)
async def docs_page(page: str, request: Request):
    """返回指定文档页（自动补 .html 后缀）。"""
    docs_dir: Path | None = getattr(request.app.state, "docs_dir", None)
    if docs_dir is None:
        raise HTTPException(status_code=503, detail="docs 目录未配置")

    # 自动补 .html 后缀
    name = page if page.endswith(".html") else f"{page}.html"
    target = _resolve_safe(docs_dir, name)
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"页面 {page} 不存在")
    return FileResponse(str(target), media_type="text/html")
