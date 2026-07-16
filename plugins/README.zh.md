# plugins/

每个 `.py` 文件 = 一个**独立子进程**运行的上游数据源插件。

主程序 [`PluginManager`](../../app/plugins/manager.py) 在启动时扫描本目录（按文件名排序），对每个插件文件以 `python plugins/<file>.py <callback_url> <token>` 方式 fork 子进程，并通过 `push` / `report_error` 回调把上游数据写回主进程的内存缓存。

> 项目默认语言为英文。英文版见 [`README.md`](./README.md)。

## 目录约定

- 文件名随意（建议用 `snake_case`，反映数据源或用途）
- 文件内必须定义 `PLUGIN_CONFIG`（插件元数据）和 `async def start(callback_url, token)`（入口函数）
- 跨插件 endpoint path **必须全局唯一**，否则 `PluginLoader.scan_directory` 抛 `DuplicateEndpointError` 并拒绝启动
- 删除插件：直接删文件即可，下次启动不再加载
- 本仓库**不内置任何插件** —— 用户按下方指南自行编写并放入本目录

## 快速开始

1. 复制下方"最小骨架"代码保存为 `plugins/my_source.py`
2. 填 `PLUGIN_CONFIG` 和 `start()` 里的业务逻辑
3. 启动 `python server.py` —— 主程序扫描到 `my_source.py` 自动 fork 子进程

---

# 写一个 API 插件

## 1. 最小骨架

```python
"""我的数据源插件。"""

# pylint: disable=duplicate-code

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from app.plugins._runtime import safe_push, supervise_plugin
from app.plugins.client import PluginClient

logger = logging.getLogger(__name__)

# -------- PLUGIN_CONFIG --------
ENDPOINTS: list[dict[str, Any]] = [
    {
        "path": "/api/my-source",
        "protocols": ["http", "ws"],
        "exclude_from_all": False,
    },
]

PLUGIN_CONFIG: dict[str, Any] = {
    "name": "my_plugin",
    "token": "my-plugin-change-me",  # 实际项目请走环境变量
    "description": "一行说明这个数据源是干嘛的",
    "endpoints": ENDPOINTS,
    "fields": {},  # plugin 级字段文档（可空）
}

# -------- 业务逻辑 --------
async def _run(client: PluginClient, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            data = await fetch_from_upstream()  # 你自己实现
            await safe_push(client, "/api/my-source", data)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            await safe_report_error(client, "/api/my-source", "UPSTREAM_FAIL", str(exc))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            pass

# -------- 入口 --------
async def start(
    callback_url: str,
    token: str,
    *,
    client: PluginClient | None = None,
) -> None:
    owns_client = client is None
    if client is None:
        client = PluginClient(callback_url, token)
    stop_event = asyncio.Event()
    tasks = [asyncio.create_task(_run(client, stop_event), name="my_plugin:run")]
    await supervise_plugin(tasks, stop_event, client, owns_client=owns_client)

if __name__ == "__main__":  # pylint: disable=duplicate-code
    _cb_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    _cb_tok = sys.argv[2] if len(sys.argv) > 2 else PLUGIN_CONFIG["token"]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(start(_cb_url, _cb_tok))
```

> **放进 `plugins/` 之后无需注册**：下次 `python server.py` 启动时 `PluginLoader.scan_directory` 会自动发现并加载。

---

## 2. `PLUGIN_CONFIG` 字段规范

顶层字段（`name` / `token` / `endpoints` **必填**；`description` / `fields` 可选）：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | `str` | ✅ | 插件名，也是 `/all` JSON 顶层 key。需与文件名含义一致，建议 snake_case |
| `token` | `str` | ✅ | 回调鉴权用 Bearer token。生产请走环境变量 |
| `description` | `str` | ❌ | 一句话说明上游来源 / 用途 |
| `endpoints` | `list[dict]` | ✅ | 该插件暴露的 endpoint，**非空** |
| `fields` | `dict` | ❌ | plugin 级字段文档（可空 `{}`），per-endpoint 可覆盖 |

`endpoints[].` 字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `path` | `str` | ✅ | 必须以 `/api/` 开头；跨插件全局唯一 |
| `protocols` | `list[str]` | ✅ | `["http", "ws"]` 的非空子集；只声明要支持的协议 |
| `exclude_from_all` | `bool` | ❌ | 默认 `False`；为 `True` 时本 endpoint 不出现在 `/all` 聚合消息里（用于必须独立连接的情况） |
| `description` | `str` | ❌ | per-endpoint 说明，会覆盖 plugin 级 |
| `fields` | `dict` | ❌ | per-endpoint 字段文档；非空时**整体替换** plugin 级 |

合法示例：

```python
"endpoints": [
    # HTTP + WS 双协议
    {"path": "/api/cwa", "protocols": ["http", "ws"]},
    # 仅 WS
    {"path": "/api/realtime", "protocols": ["ws"]},
    # 从 /all 聚合排除（独立连接）
    {"path": "/api/cenc-ir", "protocols": ["http", "ws"], "exclude_from_all": True},
    # 带 per-endpoint 文档
    {
        "path": "/api/my-source",
        "protocols": ["http", "ws"],
        "description": "我的数据源",
        "fields": {
            "lat": {"type": "number", "description": "震中纬度"},
        },
    },
]
```

非法示例（启动期会被 `PluginLoader` 拒掉）：

```python
# ❌ 路径不以 /api/ 开头
{"path": "/cwa", "protocols": ["http", "ws"]}

# ❌ protocols 为空
{"path": "/api/x", "protocols": []}

# ❌ protocols 含非法值
{"path": "/api/x", "protocols": ["http", "grpc"]}

# ❌ endpoints 为空
"endpoints": []
```

---

## 3. SDK：`app.plugins.client.PluginClient`

`PluginClient` 是子进程与主程序之间的**回调客户端**。所有方法都是 `async`，`push` 失败会抛异常，调用方需自行处理或用 `_runtime` 提供的 `safe_*` 包装。

| 方法 | 用途 |
|---|---|
| `await client.push(endpoint, data, source=None, md5=None)` | 推一条数据回主程序缓存。`md5` 不传会自动 `compute_md5`；`source` 不传会从 endpoint 推导（剥掉 `/api/` 前缀） |
| `await client.report_error(endpoint, error_type, message, details=None)` | 上报一条错误事件（出现在 `/ws/api/<endpoint>` 的 error 帧和 `/all` 错误帧里） |
| `await client.close()` | 关闭底层 `httpx.AsyncClient`（用 `supervise_plugin(..., owns_client=True)` 时**不要**手动 close） |

错误类型建议命名（`error_type`）：

- `FETCH_FAILED`：HTTP 拉取失败（网络、超时、非 2xx）
- `PARSE_FAILED`：上游返回体解析失败（JSON 损坏、结构异常）
- `PUSH_FAILED`：推回主程序失败（主程序挂了 / token 不对 / 内部错误）— 通常由 `safe_push` 自动上报
- `UPSTREAM_TIMEOUT` / `WEBSOCKET_CLOSED`：其他场景

---

## 4. SDK：`app.plugins._runtime` 共享 helper

减少每个插件重复样板：

| 函数 | 用途 |
|---|---|
| `path_to_source(path)` | `"/api/cwa"` → `"cwa"`；用于 `/all` 路由 |
| `await safe_push(client, endpoint, data, *, md5=None)` | 调 `client.push`，失败时**自动**上报 `PUSH_FAILED` 并吞掉异常 |
| `await safe_report_error(client, endpoint, error_type, message, details=None)` | 调 `client.report_error`，自身失败静默（不阻塞循环） |
| `await supervise_plugin(tasks, stop_event, client=None, *, owns_client=False)` | 用 `asyncio.gather(..., return_exceptions=True)` 统一管 task 生命周期；`CancelledError` 触发时 set stop_event 并 cancel 全部；`owns_client=True` 时退出前关 client |

**推荐组合**：

```python
async def start(callback_url, token, *, client=None):
    owns_client = client is None
    if client is None:
        client = PluginClient(callback_url, token)
    stop_event = asyncio.Event()
    tasks = [
        asyncio.create_task(_consume_all(client, stop_event),  name="me:/all"),
        asyncio.create_task(_consume_one(client, stop_event), name="me:/x"),
    ]
    await supervise_plugin(tasks, stop_event, client, owns_client=owns_client)
```

---

## 5. 两种常见模式

### A. WebSocket 消费

```python
import json
import websockets

RECONNECT_DELAY = 3.0
RECONNECT_MAX_DELAY = 30.0

async def _consume(client, stop_event, url, route):
    delay = RECONNECT_DELAY
    warned = False
    while not stop_event.is_set():
        try:
            async with websockets.connect(url, ping_interval=None) as ws:
                delay = RECONNECT_DELAY
                warned = False  # 成功后重置，本轮失败可重新告警
                async for raw in ws:
                    if stop_event.is_set():
                        break
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        continue
                    await route(client, msg)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if delay >= RECONNECT_MAX_DELAY and not warned:
                logger.warning("[%s] 持续连接失败: %s", url, exc)
                warned = True
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, RECONNECT_MAX_DELAY)
```

### B. HTTP 轮询

```python
import httpx
from app.plugins.client import compute_md5

POLL_INTERVAL = 60.0

async def _poll(client_http, client, stop_event, endpoint, url):
    last_md5: str | None = None
    while not stop_event.is_set():
        try:
            resp = await client_http.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        except asyncio.CancelledError:
            return
        except Exception as exc:  # pylint: disable=broad-exception-caught
            await safe_report_error(client, endpoint, "FETCH_FAILED", str(exc))
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL)
            except asyncio.TimeoutError:
                pass
            continue
        md5 = compute_md5(data)
        if md5 != last_md5:           # 仅数据变化才推
            last_md5 = md5
            await safe_push(client, endpoint, data)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
```

---

## 6. 日志约定

- **只用** `INFO` / `WARNING` / `ERROR` 三档
- 启动时打**一行** `INFO` 说明本插件维护多少条连接 / 多少个 endpoint
- 正常 `connecting` / `connected` **不**打日志（避免重连风暴刷屏）
- 持续连接失败（backoff 达到上限）**每个失败周期只记一次** `WARNING`，重连成功后重置
- 干净断开（同 `async for` 自然结束）静默，不打 WARNING
- 业务错误用 `safe_report_error`，不要 `logger.error`（错误会通过 WS 推给订阅者）

---

## 7. 文档说明

每个 endpoint 在生成的静态文档里都会显示 description + fields 表。

**优先级**（每项独立判定）：

```
docs_overrides.yaml（项目根）
    > PLUGIN_CONFIG.endpoints[].description / fields
    > PLUGIN_CONFIG.description / fields
```

- 插件内只写**最简默认**（够文档站不显示空就行）
- 用户在 `docs_overrides.yaml` 里**覆盖**（不改代码即可发布/修改文档）
- `fields` 是**整体替换**，不是按 key merge；这样避免覆盖字段与默认字段结构不一致时文档错位

`docs_overrides.yaml` 用法见该文件头部注释。

---

## 8. 测试约定

测试文件放 `tests/`，命名 `test_<plugin>_plugin.py`。

需要把 `PluginClient` 替成 `_FakeClient` 桩：

```python
class _FakeClient:
    def __init__(self):
        self.calls = []     # push 记录
        self.errors = []    # report_error 记录

    async def push(self, endpoint, data, source=None, md5=None):
        self.calls.append({"endpoint": endpoint, "data": data, "source": source, "md5": md5})
        return {"ok": True}

    async def report_error(self, endpoint, error_type, message, details=None):
        self.errors.append({"endpoint": endpoint, "error_type": error_type, "message": message, "details": details})
        return {"ok": True}

    async def close(self):
        pass
```

由于桩的类型不是真正的 `PluginClient`，调用 `safe_push(fake, ...)` 时加 `# type: ignore[arg-type]`，避免 Pylance 报错。

跑测试：`python -m pytest tests/ -q`

---

## 9. 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 启动报 `DuplicateEndpointError` | path 已被其他插件占 | 改名；或合并到那个插件 |
| 启动报 `endpoints 必须是非空 list` | `endpoints` 字段缺失或为空 | 至少 1 个 |
| 启动报 `endpoints[0].path 必须以 /api/ 开头` | path 漏前缀 | 改成 `/api/xxx` |
| WS 持续 WARNING | 上游不可达 | 检查网络；token 不对也会 401 |
| 文档页某 endpoint `fields` 为空 | 没在插件里写、也没在 `docs_overrides.yaml` 写 | 二选一补上 |
| 端点收不到数据 | 回调 token 与 `PLUGIN_CONFIG.token` 不一致 | 主程序用 `manager.py` 启动时分配的 token；插件 `PLUGIN_CONFIG.token` 也得跟上 |
