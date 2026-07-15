"""CLI 入口，支持 --host/--port/--no-auto-start 等参数。"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from server import (
    DEFAULT_DOCS_DIR,
    DEFAULT_HOST,
    DEFAULT_LOGS_DIR,
    DEFAULT_PLUGINS_DIR,
    DEFAULT_PORT,
    _serve,
)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    p = argparse.ArgumentParser(description="API aggregator 启动入口")
    p.add_argument("--plugins", type=Path, default=DEFAULT_PLUGINS_DIR, help="插件目录")
    p.add_argument("--host", default=DEFAULT_HOST, help="监听地址")
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help="监听端口")
    p.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR, help="文档输出目录")
    p.add_argument("--log-dir", type=Path, default=DEFAULT_LOGS_DIR, help="日志目录")
    p.add_argument(
        "--no-auto-start",
        action="store_true",
        help="不自动拉起插件子进程（仅启动 HTTP/WS 服务）",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别",
    )
    return p.parse_args()


def main() -> None:
    """解析参数并启动服务。"""
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(
            _serve(
                host=args.host,
                port=args.port,
                plugins_dir=args.plugins,
                docs_dir=args.docs_dir,
                log_dir=args.log_dir,
                auto_start=not args.no_auto_start,
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
