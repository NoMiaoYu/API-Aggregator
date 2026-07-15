"""浏览器测试入口：加载插件 → 生成文档 → 启动 uvicorn → 拉起插件子进程。

默认相对路径：plugins/、docs/、logs/。
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

import uvicorn

from app import __version__
from app.main import create_app, generate_docs, load_plugins_from_dir
from app.plugins.manager import PluginManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("server")


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PLUGINS_DIR = PROJECT_ROOT / "plugins"
DEFAULT_DOCS_DIR = PROJECT_ROOT / "docs"
DEFAULT_LOGS_DIR = PROJECT_ROOT / "logs"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


async def _serve(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    plugins_dir: Path = DEFAULT_PLUGINS_DIR,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    log_dir: Path = DEFAULT_LOGS_DIR,
    auto_start: bool = True,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)

    # 1) 加载插件
    plugins = load_plugins_from_dir(plugins_dir)
    logger.info("已加载 %d 个插件", len(plugins))
    for p in plugins:
        logger.info(
            "  - %s: %s",
            p.name,
            ", ".join(f"{ep.path} [{'/'.join(ep.protocols)}]" for ep in p.endpoints),
        )

    # 2) 生成文档
    generate_docs(plugins, version=__version__, output_dir=docs_dir)
    logger.info("文档已生成到 %s", docs_dir)

    # 3) 创建 app
    app = create_app(
        cache=None,  # 用默认 DataCache
        plugins=plugins,
        log_dir=log_dir,
        docs_dir=docs_dir,
    )

    # 4) PluginManager
    callback_url = f"http://{host}:{port}"
    manager = PluginManager(
        callback_url=callback_url,
        project_root=PROJECT_ROOT,
        plugin_dir=plugins_dir,
    )

    # 5) 启动 uvicorn
    config = uvicorn.Config(
        app, host=host, port=port, log_level="info", lifespan="on"
    )
    server = uvicorn.Server(config)

    server_task = asyncio.create_task(server.serve(), name="uvicorn")
    # 等 uvicorn 进入 serving
    for _ in range(50):
        if server.started:
            break
        await asyncio.sleep(0.1)
    if not server.started:
        logger.error("uvicorn 启动超时")
        return

    # 6) 启动插件
    if auto_start and plugins:
        for plugin in plugins:
            await manager.start(plugin.name, plugin)

    base = f"http://{host}:{port}"
    logger.info("=" * 60)
    logger.info("服务已就绪 v%s", __version__)
    logger.info("  HTTP 列表:    %s/", base)
    logger.info("  HTTP /api/all (会返回 405): %s/api/all", base)
    logger.info("  WS   /ws/all:  ws://%s:%d/ws/all", host, port)
    logger.info("  文档首页:     %s/doc/ws-api/", base)
    logger.info("=" * 60)
    logger.info("按 Ctrl+C 退出")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_signal():
        logger.info("收到退出信号，开始优雅关闭...")
        stop_event.set()
        server.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except NotImplementedError:
            # Windows 上 SIGTERM 不可用
            pass

    # 等停止信号或 server 自己退出
    await stop_event.wait()
    await server_task
    # 关闭所有插件
    await manager.stop_all()
    logger.info("已退出")


def main() -> None:
    """CLI 入口：捕获 Ctrl+C 后安静退出。"""
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
