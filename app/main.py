"""FastAPI 应用工厂 + 插件加载 + 文档生成入口。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI

from app import __version__
from app.core.cache import DataCache
from app.plugins.config import PluginConfig, PluginLoader

logger = logging.getLogger(__name__)


def load_plugins_from_dir(plugins_dir: str | Path) -> list[PluginConfig]:
    """从目录扫描并加载所有插件配置。"""
    return PluginLoader.scan_directory(plugins_dir)


def create_app(
    cache: DataCache | None = None,
    plugins: Iterable[PluginConfig] | None = None,
    log_dir: str | Path | None = None,
    docs_dir: str | Path | None = None,
) -> FastAPI:
    """创建 FastAPI 实例。"""
    # 先放占位 import，循环依赖里 callback 先于 router 出现也 OK
    from app.api.callbacks import router as callbacks_router
    from app.api.docs import router as docs_router
    from app.api.endpoints import router as http_router
    from app.api.websocket import router as ws_router

    app = FastAPI(title="API Aggregator", version=__version__)

    # 挂载状态
    app.state.cache = cache or DataCache()
    app.state.plugins = list(plugins) if plugins is not None else []
    app.state.log_dir = Path(log_dir) if log_dir is not None else None
    app.state.docs_dir = Path(docs_dir) if docs_dir is not None else None
    app.state.version = __version__

    # 路由
    app.include_router(callbacks_router, tags=["internal"])
    app.include_router(http_router, tags=["http"])
    app.include_router(ws_router, tags=["ws"])
    app.include_router(docs_router)

    @app.get("/")
    async def root():
        return {
            "name": "api-aggregator",
            "version": __version__,
            "endpoints": [
                ep.path for p in app.state.plugins for ep in p.endpoints
            ],
        }

    return app


def generate_docs(plugins: list[PluginConfig], version: str, output_dir: str | Path) -> None:
    """生成静态文档（实际渲染在 docs_gen/ 模块里）。"""
    from app.docs_gen.generator import DocsGenerator

    DocsGenerator(plugins, version=version, output_dir=output_dir).generate()
