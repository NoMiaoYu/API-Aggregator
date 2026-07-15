"""PluginManager 单元测试。"""

from __future__ import annotations

import asyncio
import textwrap
import time
from pathlib import Path

import pytest

from app.plugins.config import EndpointSpec, PluginConfig
from app.plugins.manager import PluginManager, PluginState


def _write_sleeper_plugin(path: Path, *, name: str = "fan_weather", endpoint: str = "/api/weather"):
    """写一个 dummy 插件：sleep 等待回调 URL + token，足够长以容纳测试。"""
    body = f'''
        import asyncio, sys

        PLUGIN_CONFIG = {{
            "name": "{name}",
            "token": "tok-{name}",
            "description": "d",
            "endpoints": [
                {{"path": "{endpoint}", "protocols": ["http", "ws"]}},
            ],
            "fields": {{}},
        }}

        async def start(callback_url, token):
            # 长驻循环直到被强杀
            while True:
                await asyncio.sleep(60)
        '''
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def _write_immediate_exit_plugin(path: Path, *, name: str = "crashy"):
    body = f'''
        PLUGIN_CONFIG = {{
            "name": "{name}",
            "token": "tok",
            "description": "d",
            "endpoints": [{{"path": "/api/x", "protocols": ["http"]}}],
            "fields": {{}},
        }}

        async def start(callback_url, token):
            import sys; sys.exit(0)
        '''
    path.write_text(textwrap.dedent(body), encoding="utf-8")


async def _wait_state(manager: PluginManager, name: str, target: PluginState, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if manager.get_state(name) == target:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(
        f"plugin {name} 状态在 {timeout}s 内未变成 {target}，"
        f"当前={manager.get_state(name)}"
    )


@pytest.mark.asyncio
async def test_start_runs_plugin(tmp_path: Path):
    plugin_path = tmp_path / "p.py"
    _write_sleeper_plugin(plugin_path)
    cfg = PluginConfig(
        name="fan_weather", token="tok-fan_weather", description="d",
        fields={}, endpoints=[EndpointSpec(path="/api/weather", protocols=["http", "ws"])],
        file_path=plugin_path,
    )
    mgr = PluginManager(callback_url="http://127.0.0.1:8000", project_root=tmp_path)
    try:
        await mgr.start("fan_weather", cfg)
        await _wait_state(mgr, "fan_weather", PluginState.RUNNING)
    finally:
        await mgr.stop_all()


@pytest.mark.asyncio
async def test_stop_returns_to_stopped(tmp_path: Path):
    plugin_path = tmp_path / "p.py"
    _write_sleeper_plugin(plugin_path)
    cfg = PluginConfig(
        name="fan_weather", token="tok", description="d",
        fields={}, endpoints=[EndpointSpec(path="/api/weather", protocols=["http"])],
        file_path=plugin_path,
    )
    mgr = PluginManager(callback_url="http://127.0.0.1:8000", project_root=tmp_path)
    await mgr.start("fan_weather", cfg)
    await _wait_state(mgr, "fan_weather", PluginState.RUNNING)
    await mgr.stop("fan_weather")
    assert mgr.get_state("fan_weather") == PluginState.STOPPED


@pytest.mark.asyncio
async def test_restart_keeps_running(tmp_path: Path):
    plugin_path = tmp_path / "p.py"
    _write_sleeper_plugin(plugin_path)
    cfg = PluginConfig(
        name="fan_weather", token="tok", description="d",
        fields={}, endpoints=[EndpointSpec(path="/api/weather", protocols=["http"])],
        file_path=plugin_path,
    )
    mgr = PluginManager(callback_url="http://127.0.0.1:8000", project_root=tmp_path)
    try:
        await mgr.start("fan_weather", cfg)
        await _wait_state(mgr, "fan_weather", PluginState.RUNNING)
        first_proc = mgr._procs["fan_weather"].proc
        assert first_proc is not None

        await mgr.restart("fan_weather")
        await _wait_state(mgr, "fan_weather", PluginState.RUNNING)
        second_proc = mgr._procs["fan_weather"].proc
        assert second_proc is not None
        # 进程对象应当不一样
        assert second_proc is not first_proc
    finally:
        await mgr.stop_all()


@pytest.mark.asyncio
async def test_stop_all_brings_all_plugins_to_stopped(tmp_path: Path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    _write_sleeper_plugin(a, name="a", endpoint="/api/a")
    _write_sleeper_plugin(b, name="b", endpoint="/api/b")
    cfg_a = PluginConfig(
        name="a", token="t-a", description="d", fields={},
        endpoints=[EndpointSpec(path="/api/a", protocols=["http"])],
        file_path=a,
    )
    cfg_b = PluginConfig(
        name="b", token="t-b", description="d", fields={},
        endpoints=[EndpointSpec(path="/api/b", protocols=["http"])],
        file_path=b,
    )
    mgr = PluginManager(callback_url="http://127.0.0.1:8000", project_root=tmp_path)
    await mgr.start("a", cfg_a)
    await mgr.start("b", cfg_b)
    await _wait_state(mgr, "a", PluginState.RUNNING)
    await _wait_state(mgr, "b", PluginState.RUNNING)
    await mgr.stop_all()
    assert mgr.get_state("a") == PluginState.STOPPED
    assert mgr.get_state("b") == PluginState.STOPPED


@pytest.mark.asyncio
async def test_endpoint_to_plugin_reverse_index(tmp_path: Path):
    plugin_path = tmp_path / "p.py"
    _write_sleeper_plugin(plugin_path, endpoint="/api/weather")
    cfg = PluginConfig(
        name="fan_weather", token="t", description="d", fields={},
        endpoints=[EndpointSpec(path="/api/weather", protocols=["http", "ws"])],
        file_path=plugin_path,
    )
    mgr = PluginManager(callback_url="http://127.0.0.1:8000", project_root=tmp_path)
    await mgr.start("fan_weather", cfg)
    try:
        assert mgr.get_plugin_for_endpoint("/api/weather") == "fan_weather"
        assert mgr.get_plugin_for_endpoint("/api/missing") is None
    finally:
        await mgr.stop_all()
    # stop 后反向索引应清理
    assert mgr.get_plugin_for_endpoint("/api/weather") is None


@pytest.mark.asyncio
async def test_plugin_dir_is_passed_to_subprocess(tmp_path: Path):
    """子进程能 import app.plugins.client（因为 PYTHONPATH 注入）。"""
    plugin_path = tmp_path / "p.py"
    # 写一个能 self-check 的 plugin：import app.plugins.client 后退出
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d", "fields": {},
            "endpoints": [{"path": "/api/x", "protocols": ["http"]}],
        }
        async def start(callback_url, token):
            from app.plugins.client import PluginClient  # noqa: F401
            import asyncio
            await asyncio.sleep(60)
    '''
    plugin_path.write_text(textwrap.dedent(body), encoding="utf-8")
    cfg = PluginConfig(
        name="n", token="t", description="d", fields={},
        endpoints=[EndpointSpec(path="/api/x", protocols=["http"])],
        file_path=plugin_path,
    )
    mgr = PluginManager(callback_url="http://127.0.0.1:8000", project_root=Path(__file__).resolve().parents[1])
    await mgr.start("n", cfg)
    try:
        await _wait_state(mgr, "n", PluginState.RUNNING)
    finally:
        await mgr.stop_all()


@pytest.mark.asyncio
async def test_restart_after_crash_does_not_create_new_watcher(tmp_path: Path):
    """watcher 应该是 plugin 生命周期内唯一的，restart 时复用同一个。"""
    plugin_path = tmp_path / "p.py"
    _write_immediate_exit_plugin(plugin_path, name="crashy")
    cfg = PluginConfig(
        name="crashy", token="t", description="d", fields={},
        endpoints=[EndpointSpec(path="/api/x", protocols=["http"])],
        file_path=plugin_path,
    )
    mgr = PluginManager(
        callback_url="http://127.0.0.1:8000",
        project_root=tmp_path,
        restart_delay=0.1,
        max_restart_attempts=3,
    )
    await mgr.start("crashy", cfg)
    try:
        # 让 watcher 触发至少一次 restart
        await asyncio.sleep(0.5)
        watcher_task = mgr._procs["crashy"].watcher
        assert watcher_task is not None
        # 显式 restart：watcher 仍应是同一个
        first_watcher = watcher_task
        await mgr.restart("crashy")
        await _wait_state(mgr, "crashy", PluginState.RUNNING)
        assert mgr._procs["crashy"].watcher is first_watcher
    finally:
        await mgr.stop_all()
