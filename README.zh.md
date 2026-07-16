# API Aggregator


[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-180%20passed-brightgreen.svg)](./tests)
[![Pylint](https://img.shields.io/badge/pylint-10.00%2F10-brightgreen.svg)](./.pylintrc)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Lang: ZH](https://img.shields.io/badge/lang-ZH%20%7C%20EN-blue.svg)](./README.md)

> 项目默认语言为英文。中文用户请通过 `config.yaml` 切换 `language: zh`。
> 在 `config.yaml` 中设置 `language: en` 可切回英文。

[English documentation](./README.md)

一个基于 **FastAPI + 插件子进程** 的实时数据聚合网关。  
每个上游数据源 = 一个独立 Python 进程；主进程负责 HTTP/WS 路由、内存缓存、文档生成、生命周期管理。

> 设计目标：上游故障**不污染**主进程，插件可热替换，文档随插件自动生成。

---

## 特性

- **进程级隔离**：每个插件独立子进程，崩溃自动重启（最多 5 次），可独立 `kill` 不影响主程序
- **统一回调协议**：插件用 `PluginClient` 把上游数据 `push` 回主进程，token 鉴权
- **HTTP + WebSocket 双协议**：每个 endpoint 声明 `protocols: ["http", "ws"]`，下游按需消费
- **聚合 WS 端点 `/ws/all`**：单条 WS 连接收到所有 `exclude_from_all=False` 端点的最新快照与增量更新
- **md5 去重**：HTTP 轮询插件仅在数据变化时推送
- **静态文档自动生成**：Jinja2 渲染 `docs/`，用户可在 `docs_overrides.yaml` 覆盖默认说明，无需改代码
- **结构化错误推送**：插件内 `report_error` → 主进程 → WS 订阅者即时看到

---

## 快速开始

### 环境要求

- Python ≥ 3.10
- Windows / macOS / Linux

### 安装

```bash
git clone https://github.com/NoMiaoYu/api-aggregator.git
cd api-aggregator
pip install -r requirements.txt
```

### 启动

```bash
python server.py
# 或带参数：
python run.py --host 0.0.0.0 --port 8000 --no-auto-start
```

启动成功后会看到：

```
============================================================
服务已就绪 v0.1.0
  HTTP 列表:    http://127.0.0.1:8000/
  WS   /ws/all:  ws://127.0.0.1:8000/ws/all
  文档首页:     http://127.0.0.1:8000/doc/ws-api/
============================================================
```

### 验证

```bash
# 列出全部 endpoint
curl http://127.0.0.1:8000/

# 读单端点最新数据
curl http://127.0.0.1:8000/api/cenc

# 订阅单端点 WS
websocat ws://127.0.0.1:8000/ws/api/cenc

# 订阅全聚合
websocat ws://127.0.0.1:8000/ws/all
```

> `/api/all` 是 WS 专用聚合端点，对它的 HTTP 请求会返回 `405 Method Not Allowed`。

### 运行测试

```bash
python -m pytest tests/ -q
```

---

## 项目结构

```
api-aggregator/
├── server.py              # 浏览器测试入口（uvicorn + 插件拉起）
├── run.py                 # CLI 入口（支持 --host/--port/--no-auto-start/...）
├── requirements.txt
├── docs_overrides.yaml    # 用户可编辑的 endpoint 文档覆盖
├── .pylintrc              # pylint 10.00/10 配套配置
│
├── app/                   # 主程序
│   ├── main.py            # FastAPI 工厂 + 插件加载 + 文档生成入口
│   ├── api/               # HTTP / WebSocket / callback / docs 路由
│   ├── core/cache.py      # 内存缓存（latest-only）
│   ├── plugins/           # 插件 SDK
│   │   ├── config.py      #   PLUGIN_CONFIG 加载与校验
│   │   ├── client.py      #   PluginClient（push / report_error）
│   │   ├── _runtime.py    #   safe_push / supervise_plugin 共享 helper
│   │   └── manager.py     #   插件子进程生命周期 + 自动重启
│   └── docs_gen/          # 静态文档生成（Jinja2）
│
├── plugins/               # 插件目录（每个 .py = 一个子进程）
│   ├── README.md          #   插件开发指南
│   └── (空)               #   用户按指南在此目录放自己的插件
│
├── logs/                  # 运行时日志（启动时自动创建，已 gitignore）
├── docs/                  # 生成的静态文档（启动时自动生成，已 gitignore）
└── tests/                 # 单元测试
```

---

## 架构

```
                    ┌─────────────────────────────┐
                    │   主进程 (server.py / uvicorn) │
                    │                              │
   HTTP /api/x ───► │  app/api/endpoints.py       │
   WS  /ws/api/x──► │  app/api/websocket.py       │
   WS  /ws/all ───► │  app/api/websocket.py  ─┐   │
                    │  app/core/cache.py ◄────┘   │
                    │  app/plugins/manager.py  │   │
                    └────────────┬─────────────────┘
                                 │ fork subprocess
            ┌────────────────────┼────────────────────┐
            ▼                    ▼                    ▼
     plugins/<your>.py     plugins/<your>.py     plugins/<your>.py
     (WS / HTTP / 轮询)      (WS / HTTP / 轮询)    (WS / HTTP / 轮询)
            │                    │                    │
            └──► PluginClient.push() ────►  /api/internal/callback/data
```

**关键点**：
- 插件是**独立进程**，主程序通过 HTTP 回调接收数据
- 一个插件可以有 N 个 endpoint（`PLUGIN_CONFIG.endpoints`），但只跑**一个**子进程
- 同一插件的多个 endpoint **共享**上游连接（自己写 plugin 时可让单条上游连接 fan-out 推多个 endpoint）
- 插件崩溃 → `PluginManager` watcher 任务自动重启（指数退避，最多 5 次）

---

## 写一个新插件

完整指南见 [`plugins/README.md`](./plugins/README.md)。最小骨架：

```python
"""我的数据源插件。"""
import asyncio, logging, sys
from typing import Any
from app.plugins._runtime import safe_push, safe_report_error, supervise_plugin
from app.plugins.client import PluginClient

logger = logging.getLogger(__name__)

ENDPOINTS = [
    {"path": "/api/my-source", "protocols": ["http", "ws"]},
]

PLUGIN_CONFIG = {
    "name": "my_plugin",
    "token": "my-plugin-change-me",
    "description": "一句话说明",
    "endpoints": ENDPOINTS,
    "fields": {},
}

async def _run(client, stop_event):
    while not stop_event.is_set():
        try:
            data = await fetch()  # 你自己实现
            await safe_push(client, "/api/my-source", data)
        except Exception as exc:
            await safe_report_error(client, "/api/my-source", "FETCH_FAILED", str(exc))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60)
        except asyncio.TimeoutError:
            pass

async def start(callback_url, token, *, client=None):
    owns_client = client is None
    if client is None:
        client = PluginClient(callback_url, token)
    stop_event = asyncio.Event()
    tasks = [asyncio.create_task(_run(client, stop_event), name="my_plugin:run")]
    await supervise_plugin(tasks, stop_event, client, owns_client=owns_client)

if __name__ == "__main__":
    _url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    _tok = sys.argv[2] if len(sys.argv) > 2 else PLUGIN_CONFIG["token"]
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    asyncio.run(start(_url, _tok))
```

放进 `plugins/` 后下次启动自动加载。

### `PLUGIN_CONFIG` 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | str | ✅ | 插件名（也是 `/all` 顶层 key） |
| `token` | str | ✅ | 回调鉴权 Bearer token |
| `description` | str | ✅ | 一句话说明 |
| `endpoints` | list | ✅ | 至少 1 项 |
| `fields` | dict | ✅ | 字段文档（可空） |

`endpoints[].`：

| 字段 | 必填 | 说明 |
|---|---|---|
| `path` | ✅ | 必须以 `/api/` 开头；跨插件全局唯一 |
| `protocols` | ✅ | `["http", "ws"]` 的非空子集 |
| `exclude_from_all` | ❌ | 默认 `False`；`True` 表示不进入 `/all` 聚合 |
| `description` / `fields` | ❌ | per-endpoint 文档，覆盖 plugin 级 |

---

## 文档系统

### 三层优先级

```
docs_overrides.yaml（项目根）
    > PLUGIN_CONFIG.endpoints[].description / fields
    > PLUGIN_CONFIG.description / fields
```

### 用户覆盖 `docs_overrides.yaml`

```yaml
endpoints:
  /api/cwa:
    description: |
      在此填写这个 endpoint 的功能、数据来源、更新频率等。
    fields:
      field_name:
        type: string
        nullable: false
        description: 字段说明
```

启动时自动重新生成 `docs/`，并在 `http://127.0.0.1:8000/doc/ws-api/` 暴露。

> `fields` 是**整体替换**而非按 key 合并 —— 这避免覆盖/默认字段定义不一致时文档错位。

---

## API 协议

### 内部回调（插件 → 主程序）

```
POST /api/internal/callback/data
Authorization: Bearer <token>
Content-Type: application/json

{
  "endpoint": "/api/cwa",
  "data": { ... 任意 JSON ... },
  "md5": "abc123...",        // 可选，缺省时主程序计算
  "timestamp": 1721000000,
  "source": "cwa"
}
```

```
POST /api/internal/callback/error
Authorization: Bearer <token>

{
  "endpoint": "/api/cwa",
  "error_type": "FETCH_FAILED",
  "message": "timeout",
  "details": { ... },
  "timestamp": 1721000000
}
```

### HTTP 客户端

```
GET /api/<endpoint>
→ 200 + 最新数据（JSON）
→ 404 endpoint 不存在
→ 503 尚未收到任何数据
```

### WebSocket

```
WS /ws/api/<endpoint>
→ {"type": "snapshot", "endpoint": "...", "data": {...}, "timestamp": ...}
→ {"type": "update",   "endpoint": "...", "data": {...}, "timestamp": ...}
→ {"type": "error",    "endpoint": "...", "error_type": "...", "message": "..."}

WS /ws/all
→ 首次：{"type": "initial_all", "<source>": {Data, md5}, ...}
→ 增量：{"type": "update", "source": "...", "Data": ..., "md5": "..."}
```

---

## 开发约定

- **代码质量**：`python -m pylint --rcfile=.pylintrc plugins/ app/ run.py server.py` 维持 10.00/10
- **测试**：`python -m pytest tests/ -q`，161 个用例
- **日志**：只用 `INFO` / `WARNING` / `ERROR`；正常连接不刷；持续失败每周期一次 WARNING
- **错误类型命名**：`FETCH_FAILED` / `PARSE_FAILED` / `PUSH_FAILED` / `UPSTREAM_TIMEOUT` / `WEBSOCKET_CLOSED`

---

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 启动报 `DuplicateEndpointError` | path 已被其他插件占 | 改名；或合并到那个插件 |
| 启动报 `endpoints 必须是非空 list` | `endpoints` 字段缺失或为空 | 至少 1 个 |
| 启动报 `endpoints[0].path 必须以 /api/ 开头` | path 漏前缀 | 改成 `/api/xxx` |
| 插件持续 WARNING | 上游不可达 / token 不对 | 检查网络；确认 `PLUGIN_CONFIG.token` 与 `manager.py` 分配的一致 |
| 文档页某 endpoint `fields` 为空 | 没在插件里写、也没在 `docs_overrides.yaml` 写 | 二选一补上 |
| 端口 8000 被占用 | 旧进程未退出 | 换端口 `python run.py --port 8001`，或 `lsof -i:8000` / Windows `netstat` 查 PID |

---

## 路线图

- [ ] 插件热加载（无需重启主程序）

---

## 许可证

MIT
