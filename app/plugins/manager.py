"""插件子进程生命周期管理。

按 plugin 名（`name`）管理一个独立 Python 子进程。同一 plugin 的多个 endpoint
共享一个子进程、一条上游连接，靠插件内 fan-out 推送多个 endpoint。

进程退出时由 watcher 任务自动 restart（最多 max_restart_attempts 次），
重启时只 _spawn 新进程，**不**再创建新 watcher，避免并发读同一进程的 stdout stream
而抛 ValueError。
"""

from __future__ import annotations

import asyncio
import enum
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from app.plugins.config import PluginConfig

logger = logging.getLogger(__name__)


class PluginState(str, enum.Enum):
    """单个插件子进程的运行状态。"""

    STOPPED = "stopped"
    RUNNING = "running"
    FAILED = "failed"  # 超过最大重启次数后置为 FAILED


class _PluginProc:
    """单个插件的运行时状态。"""

    def __init__(self, config: PluginConfig) -> None:
        self.config = config
        self.proc: Optional[asyncio.subprocess.Process] = None
        self.state: PluginState = PluginState.STOPPED
        self.restart_count: int = 0
        # watcher 是 plugin 生命周期内唯一的协程任务，restart 时不重建
        self.watcher: Optional[asyncio.Task] = None
        # 显式 stop 信号：watcher 看到后不再尝试 restart
        self.stop_requested: bool = False


class PluginManager:
    """管理所有插件子进程。"""

    def __init__(
        self,
        callback_url: str,
        python: str | None = None,
        restart_delay: float = 2.0,
        max_restart_attempts: int = 5,
        project_root: str | Path | None = None,
        plugin_dir: str | Path | None = None,
    ) -> None:
        self.callback_url = callback_url
        self.python = python or sys.executable
        self.restart_delay = restart_delay
        self.max_restart_attempts = max_restart_attempts
        self.project_root = (
            Path(project_root).resolve() if project_root is not None else Path.cwd().resolve()
        )
        self.plugin_dir = Path(plugin_dir).resolve() if plugin_dir is not None else None
        # name -> _PluginProc
        self._procs: dict[str, _PluginProc] = {}
        # endpoint path -> plugin name
        self._endpoint_to_plugin: dict[str, str] = {}

    # ---------- 查询 ----------

    def get_state(self, name: str) -> PluginState:
        """查询某 plugin 的状态。"""
        p = self._procs.get(name)
        if p is None:
            return PluginState.STOPPED
        return p.state

    def get_plugin_for_endpoint(self, endpoint: str) -> str | None:
        """通过 endpoint path 反查所属 plugin 名。"""
        return self._endpoint_to_plugin.get(endpoint)

    def plugins(self) -> list[str]:
        """返回所有已注册 plugin 名。"""
        return list(self._procs.keys())

    # ---------- 生命周期 ----------

    async def start(self, name: str, config: PluginConfig) -> None:
        """启动一个 plugin 子进程。幂等：重复调用不会重启。"""
        if name in self._procs and self._procs[name].state == PluginState.RUNNING:
            return
        p = _PluginProc(config)
        p.stop_requested = False
        self._procs[name] = p
        # 注册 endpoint 反向索引
        for ep in config.endpoints:
            self._endpoint_to_plugin[ep.path] = name
        # 第一次启动：创建 watcher
        p.watcher = asyncio.create_task(self._watch(name, p), name=f"watch:{name}")
        # 触发首次 spawn
        await self._spawn(name, p)

    async def stop(self, name: str) -> None:
        """停止一个 plugin 子进程并取消 watcher。"""
        p = self._procs.get(name)
        if p is None:
            return
        p.stop_requested = True
        await self._terminate_proc(p)
        if p.watcher is not None:
            p.watcher.cancel()
            try:
                await p.watcher
            except (asyncio.CancelledError, RuntimeError) as exc:
                # 取消任务应抛 CancelledError；RuntimeError 可能来自 watcher 异常退出
                logger.debug("[plugin:%s] watcher 关闭时异常: %s", name, exc)
        p.watcher = None
        p.state = PluginState.STOPPED
        # 清理 endpoint 反向索引
        for ep in p.config.endpoints:
            if self._endpoint_to_plugin.get(ep.path) == name:
                self._endpoint_to_plugin.pop(ep.path, None)

    async def restart(self, name: str) -> None:
        """显式重启一个 plugin 子进程（重置 restart 计数）。"""
        p = self._procs.get(name)
        if p is None:
            return
        await self._terminate_proc(p)
        # 重置重启计数：restart 是显式行为，不算崩溃
        p.restart_count = 0
        await self._spawn(name, p)

    async def stop_all(self) -> None:
        """并发停止所有 plugin。"""
        await asyncio.gather(
            *(self.stop(n) for n in list(self._procs.keys())),
            return_exceptions=True,
        )

    # ---------- 内部 ----------

    def _build_env(self) -> dict[str, str]:
        """构造子进程环境变量：PYTHONPATH 头部追加 project_root。"""
        env = os.environ.copy()
        # 注入 project_root 到 PYTHONPATH 头部，让插件能 import app.plugins.client
        existing = env.get("PYTHONPATH", "")
        parts = [str(self.project_root)]
        if existing:
            parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(parts)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env

    async def _spawn(self, name: str, p: _PluginProc) -> None:
        """拉起子进程并启动 stdout/stderr reader。"""
        config = p.config
        env = self._build_env()
        try:
            proc = await asyncio.create_subprocess_exec(
                self.python,
                str(config.file_path),
                self.callback_url,
                config.token,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (OSError, ValueError) as exc:
            # 启动子进程可能因可执行文件缺失/参数错误而失败
            logger.error("[plugin:%s] 启动失败: %s", name, exc)
            p.state = PluginState.FAILED
            return

        p.proc = proc
        p.state = PluginState.RUNNING
        logger.info(
            "[plugin:%s] 已启动 pid=%s file=%s", name, proc.pid, config.file_path
        )
        # 起两个 reader 把 stdout/stderr 行写入 logger
        asyncio.create_task(self._pipe_reader(name, proc.stdout, logging.INFO))
        asyncio.create_task(self._pipe_reader(name, proc.stderr, logging.ERROR))

    async def _pipe_reader(self, name: str, stream, level: int) -> None:
        """把子进程 stdout/stderr 一行一行写入 logger。"""
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    return
                msg = line.decode("utf-8", errors="replace").rstrip()
                logger.log(level, "[plugin:%s] %s", name, msg)
        except (OSError, ValueError) as exc:
            # pipe 关闭或流异常：reader 结束
            logger.debug("[plugin:%s] pipe reader 退出: %s", name, exc)

    async def _terminate_proc(self, p: _PluginProc) -> None:
        proc = p.proc
        if proc is None:
            return
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        # 排空 stdout/stderr reader
        p.proc = None

    async def _watch(self, name: str, p: _PluginProc) -> None:
        """plugin 生命周期内唯一的 watcher：监控子进程退出并按策略重启。"""
        try:
            while True:
                proc = p.proc
                if proc is None:
                    # 还没 spawn 或已 stop
                    await asyncio.sleep(0.1)
                    continue
                try:
                    rc = await proc.wait()
                except asyncio.CancelledError:
                    return
                # 进程退出了
                p.proc = None
                if p.stop_requested:
                    p.state = PluginState.STOPPED
                    return
                if p.restart_count >= self.max_restart_attempts:
                    logger.error(
                        "[plugin:%s] 已达最大重启次数 %d，置 FAILED",
                        name, self.max_restart_attempts,
                    )
                    p.state = PluginState.FAILED
                    return
                p.restart_count += 1
                logger.warning(
                    "[plugin:%s] 进程退出 rc=%s，%ss 后第 %d 次重启",
                    name, rc, self.restart_delay, p.restart_count,
                )
                await asyncio.sleep(self.restart_delay)
                await self._spawn(name, p)
        except asyncio.CancelledError:
            return
        except (OSError, RuntimeError, ValueError) as exc:
            # 兜底：watcher 主循环不应被未预料的运行时错误吞掉
            logger.exception("[plugin:%s] watcher 异常: %s", name, exc)
