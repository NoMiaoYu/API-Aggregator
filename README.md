# API Aggregator


[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-180%20passed-brightgreen.svg)](./tests)
[![Pylint](https://img.shields.io/badge/pylint-10.00%2F10-brightgreen.svg)](./.pylintrc)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![Lang: EN](https://img.shields.io/badge/lang-EN%20%7C%20ZH-blue.svg)](./README.zh.md)


A real-time data aggregation gateway built on **FastAPI + plugin subprocesses**.  
Each upstream data source = one isolated Python process; the main process owns HTTP/WS routing, in-memory cache, documentation generation, and lifecycle management.

> Design goal: upstream failures **never poison** the main process, plugins are hot-swappable, docs auto-generate from plugin configs.

[中文文档](./README.zh.md)

---

## Features

- **Process-level isolation**: each plugin runs in its own subprocess, auto-restart on crash (up to 5 times), can be `kill`ed without affecting the main process
- **Unified callback protocol**: plugins use `PluginClient` to `push` upstream data back to the main process, token-authenticated
- **HTTP + WebSocket dual protocols**: each endpoint declares `protocols: ["http", "ws"]`; downstream consumers pick what they need
- **Aggregated WS endpoint `/ws/all`**: a single WebSocket connection receives latest snapshots and incremental updates for every `exclude_from_all=False` endpoint
- **md5 deduplication**: HTTP-polling plugins only push when data actually changes
- **Static docs auto-generated**: Jinja2 renders `docs/`, users can override defaults via `docs_overrides.yaml` without touching code
- **Structured error push**: `report_error` inside a plugin → main process → WS subscribers see it instantly

---

## Quick Start

### Requirements

- Python ≥ 3.10
- Windows / macOS / Linux

### Install

```bash
git clone https://github.com/NoMiaoYu/api-aggregator.git
cd api-aggregator
pip install -r requirements.txt
```

### Run

```bash
python server.py
# or with arguments:
python run.py --host 0.0.0.0 --port 8000 --no-auto-start
```

Successful startup shows:

```
============================================================
Service ready v0.1.0
  HTTP list:   http://127.0.0.1:8000/
  WS   /ws/all: ws://127.0.0.1:8000/ws/all
  Docs home:   http://127.0.0.1:8000/doc/ws-api/
============================================================
```

### Verify

```bash
# List all endpoints
curl http://127.0.0.1:8000/

# Read latest data for a single endpoint
curl http://127.0.0.1:8000/api/cenc

# Subscribe to a single endpoint over WS
websocat ws://127.0.0.1:8000/ws/api/cenc

# Subscribe to the aggregated feed
websocat ws://127.0.0.1:8000/ws/all
```

> `/api/all` is a WebSocket-only aggregator. HTTP requests to it return `405 Method Not Allowed`.

### Run tests

```bash
python -m pytest tests/ -q
```

---

## Project Structure

```
api-aggregator/
├── server.py              # Browser-test entry (uvicorn + plugin startup)
├── run.py                 # CLI entry (--host/--port/--no-auto-start/...)
├── config.yaml            # Global project config (language, server, plugins, ...)
├── docs_overrides.yaml    # User-editable endpoint doc overrides
├── requirements.txt
├── .pylintrc              # pylint config tuned to 10.00/10
│
├── app/                   # Main program
│   ├── main.py            # FastAPI factory + plugin loading + docs entry
│   ├── api/               # HTTP / WebSocket / callback / docs routes
│   ├── core/
│   │   ├── cache.py       # In-memory cache (latest-only)
│   │   └── config.py      # Global config loader (AppConfig)
│   ├── plugins/           # Plugin SDK
│   │   ├── config.py      #   PLUGIN_CONFIG loader and validator
│   │   ├── client.py      #   PluginClient (push / report_error)
│   │   ├── _runtime.py    #   safe_push / supervise_plugin shared helpers
│   │   └── manager.py     #   Subprocess lifecycle + auto-restart
│   └── docs_gen/          # Static doc generation (Jinja2)
│
├── plugins/               # Plugin directory (each .py = one subprocess)
│   ├── README.md          #   Plugin authoring guide
│   └── (empty)            #   Users drop their own plugins here
│
├── docs/                  # Generated static docs (regenerated on start)
│   ├── en/                #   English tutorials
│   └── zh/                #   Chinese tutorials
│
├── logs/                  # Runtime logs (auto-created, gitignored)
└── tests/                 # Unit tests
```

---

## Architecture

```
                    ┌─────────────────────────────┐
                    │  Main process (server.py)    │
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
     (WS / HTTP / poll)     (WS / HTTP / poll)    (WS / HTTP / poll)
            │                    │                    │
            └──► PluginClient.push() ────►  /api/internal/callback/data
```

**Key points**:
- Plugins are **separate processes**; the main process receives data via HTTP callback
- One plugin can expose N endpoints (`PLUGIN_CONFIG.endpoints`), but only spawns **one** subprocess
- Multiple endpoints of the same plugin can **share** upstream connections (your plugin code can fan-out a single upstream connection to many endpoints)
- Plugin crash → `PluginManager` watcher auto-restarts it (exponential backoff, up to 5 times)

---

## Global Configuration (`config.yaml`)

Edit the project-root `config.yaml` to change runtime behaviour; restart the server to apply. Missing keys are auto-filled with defaults (see [`app/core/config.py`](./app/core/config.py)).

```yaml
language: en            # en | zh   — UI / docs language
server:
  host: 127.0.0.1
  port: 8000
  log_level: INFO
callback:
  base_path: /api/internal/callback
plugins:
  directory: plugins
  start_timeout: 30
  max_restarts: 5
docs:
  output_dir: docs
  overrides_file: docs_overrides.yaml
```

Programmatic access:

```python
from app.core.config import AppConfig
cfg = AppConfig.load("config.yaml")
print(cfg.language)        # "en" or "zh"
print(cfg.server.port)     # 8000
print(cfg.is_zh)           # False
```

---

## Write a New Plugin

Full guide: [`plugins/README.md`](./plugins/README.md) and the [endpoint tutorial (EN)](./docs/en/endpoint-tutorial.md) / [端点教程 (中文)](./docs/zh/endpoint-tutorial.md). Minimal skeleton:

```python
"""My data-source plugin."""
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
    "description": "One-line description",
    "endpoints": ENDPOINTS,
    "fields": {},
}

async def _run(client, stop_event):
    while not stop_event.is_set():
        try:
            data = await fetch()  # your upstream fetch
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

Drop it into `plugins/` and it will be loaded automatically on the next startup.

### `PLUGIN_CONFIG` fields

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | str | ✅ | Plugin name (also the `/all` top-level key) |
| `token` | str | ✅ | Bearer token for callback auth |
| `endpoints` | list | ✅ | At least 1 entry |
| `description` | str | ❌ | Plugin-level description (overridable per-endpoint) |
| `fields` | dict | ❌ | Plugin-level field docs (overridable per-endpoint) |

`endpoints[]`:

| Field | Required | Notes |
|---|---|---|
| `path` | ✅ | Must start with `/api/`; globally unique across all plugins |
| `protocols` | ✅ | Non-empty subset of `["http", "ws"]` |
| `exclude_from_all` | ❌ | Default `False`; `True` opts out of `/all` aggregation |
| `description` / `fields` | ❌ | Per-endpoint docs, override plugin-level |

---

## Documentation System

### Three-level priority

```
docs_overrides.yaml (project root)
    > PLUGIN_CONFIG.endpoints[].description / fields
    > PLUGIN_CONFIG.description / fields
```

### User override `docs_overrides.yaml`

```yaml
endpoints:
  /api/cwa:
    description: |
      Describe what this endpoint does, data source, update frequency, etc.
    fields:
      field_name:
        type: string
        nullable: false
        description: Field description
```

Docs regenerate on startup and are served at `http://127.0.0.1:8000/doc/ws-api/`.

> `fields` is **whole-replacement** rather than key-by-key merge — this avoids doc/data drift when the override's field definition differs from the default.

---

## API Protocol

### Internal callback (plugin → main process)

```
POST /api/internal/callback/data
Authorization: Bearer <token>
Content-Type: application/json

{
  "endpoint": "/api/cwa",
  "data": { ... any JSON ... },
  "md5": "abc123...",        // optional; main process computes if absent
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

### HTTP client

```
GET /api/<endpoint>
→ 200 + latest data (JSON)
→ 404 endpoint does not exist
→ 503 no data received yet
```

### WebSocket

```
WS /ws/api/<endpoint>
→ {"type": "snapshot", "endpoint": "...", "data": {...}, "timestamp": ...}
→ {"type": "update",   "endpoint": "...", "data": {...}, "timestamp": ...}
→ {"type": "error",    "endpoint": "...", "error_type": "...", "message": "..."}

WS /ws/all
→ initial: {"type": "initial_all", "<source>": {Data, md5}, ...}
→ delta:   {"type": "update", "source": "...", "Data": ..., "md5": "..."}
```

---

## Development Conventions

- **Code quality**: `python -m pylint --rcfile=.pylintrc plugins/ app/ run.py server.py` should stay at 10.00/10
- **Tests**: `python -m pytest tests/ -q`, 180 cases
- **Logging**: only `INFO` / `WARNING` / `ERROR`; no spam on normal connect; one WARNING per persistent-failure cycle
- **Error-type names**: `FETCH_FAILED` / `PARSE_FAILED` / `PUSH_FAILED` / `UPSTREAM_TIMEOUT` / `WEBSOCKET_CLOSED`

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `DuplicateEndpointError` on startup | path is taken by another plugin | rename; or merge into that plugin |
| `endpoints 必须是非空 list` | `endpoints` missing or empty | provide at least 1 |
| `endpoints[0].path 必须以 /api/ 开头` | path missing prefix | use `/api/xxx` |
| Persistent WARNING in plugin log | upstream unreachable / token mismatch | check network; ensure `PLUGIN_CONFIG.token` matches what `manager.py` issued |
| Doc page shows empty `fields` for an endpoint | not declared in plugin nor in `docs_overrides.yaml` | fill one of the two |
| Port 8000 in use | old process not exited | use a different port `python run.py --port 8001`, or `lsof -i:8000` / Windows `netstat` to find the PID |

---

## Roadmap

- [ ] Hot-reload plugins (no main-process restart)

---

## License

MIT
