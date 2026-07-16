# plugins/

Each `.py` file = one **isolated subprocess** running an upstream data-source plugin.

> This is the English version. [中文版](./README.zh.md)

The main program ([`PluginManager`](../../app/plugins/manager.py)) scans this directory on startup (sorted by filename), forks each plugin as `python plugins/<file>.py <callback_url> <token>`, and routes the plugin's `push` / `report_error` callbacks back to the main-process in-memory cache.

## Directory conventions

- Filenames are free-form (recommend `snake_case` reflecting the data source or purpose)
- Every file must define `PLUGIN_CONFIG` (plugin metadata) and `async def start(callback_url, token)` (entry point)
- Endpoint paths must be **globally unique** across plugins; duplicates cause `PluginLoader.scan_directory` to raise `DuplicateEndpointError` and refuse startup
- To remove a plugin: just delete the file; the next startup won't load it
- This repository **ships no built-in plugins** — users write their own following the guide below

## Quick start

1. Copy the "minimal skeleton" code below and save it as `plugins/my_source.py`
2. Fill in `PLUGIN_CONFIG` and your business logic in `start()`
3. Start `python server.py` — the main program auto-detects `my_source.py` and forks a subprocess

---

# Writing an API Plugin

## 1. Minimal Skeleton

```python
"""My data-source plugin."""

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
    "token": "my-plugin-change-me",  # In production, use env vars
    "description": "One-line description of this data source",
    "endpoints": ENDPOINTS,
    "fields": {},  # plugin-level field docs (may be empty)
}

# -------- Business logic --------
async def _run(client: PluginClient, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            data = await fetch_from_upstream()  # implement yourself
            await safe_push(client, "/api/my-source", data)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            await safe_report_error(client, "/api/my-source", "UPSTREAM_FAIL", str(exc))
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            pass

# -------- Entry point --------
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

> **No registration needed after dropping into `plugins/`** — on the next `python server.py` startup, `PluginLoader.scan_directory` auto-discovers and loads it.

---

## 2. `PLUGIN_CONFIG` field reference

Top-level fields (only `name` / `token` / `endpoints` are **required**; `description` and `fields` are optional):

| Field | Type | Required | Notes |
|---|---|---|---|
| `name` | `str` | ✅ | Plugin name, also the top-level key in `/all` JSON. Match file-name meaning; recommend `snake_case`. |
| `token` | `str` | ✅ | Bearer token for callback auth. In production, use env vars. |
| `description` | `str` | ❌ | One-line summary of the upstream source / purpose. |
| `endpoints` | `list[dict]` | ✅ | Endpoints exposed by the plugin. **Must be non-empty.** |
| `fields` | `dict` | ❌ | Plugin-level field docs (may be `{}`); overridable per endpoint. |

`endpoints[]` fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `path` | `str` | ✅ | Must start with `/api/`; globally unique across plugins. |
| `protocols` | `list[str]` | ✅ | Non-empty subset of `["http", "ws"]`; only declare the protocols you support. |
| `exclude_from_all` | `bool` | ❌ | Default `False`. `True` opts the endpoint out of `/all` aggregation (for endpoints that need a dedicated connection). |
| `description` | `str` | ❌ | Per-endpoint description; overrides plugin-level. |
| `fields` | `dict` | ❌ | Per-endpoint field docs; non-empty value **whole-replaces** the plugin-level. |

Valid examples:

```python
"endpoints": [
    # HTTP + WS dual protocol
    {"path": "/api/cwa", "protocols": ["http", "ws"]},
    # WS only
    {"path": "/api/realtime", "protocols": ["ws"]},
    # Exclude from /all aggregation (dedicated connection)
    {"path": "/api/cenc-ir", "protocols": ["http", "ws"], "exclude_from_all": True},
    # With per-endpoint docs
    {
        "path": "/api/my-source",
        "protocols": ["http", "ws"],
        "description": "My data source",
        "fields": {
            "lat": {"type": "number", "description": "Epicentre latitude"},
        },
    },
]
```

Invalid examples (rejected at startup by `PluginLoader`):

```python
# ❌ Path does not start with /api/
{"path": "/cwa", "protocols": ["http", "ws"]}

# ❌ Empty protocols
{"path": "/api/x", "protocols": []}

# ❌ protocols contains invalid values
{"path": "/api/x", "protocols": ["http", "grpc"]}

# ❌ endpoints is empty
"endpoints": []
```

---

## 3. SDK: `app.plugins.client.PluginClient`

`PluginClient` is the **callback client** between the subprocess and the main program. All methods are `async`; `push` raises on failure, callers must handle it themselves or use the `safe_*` wrappers from `_runtime`.

| Method | Purpose |
|---|---|
| `await client.push(endpoint, data, source=None, md5=None)` | Push one data record back to the main-process cache. If `md5` is omitted it is auto-computed via `compute_md5`; if `source` is omitted it is derived from the endpoint (strip `/api/` prefix). |
| `await client.report_error(endpoint, error_type, message, details=None)` | Report an error event (it will appear in the `/ws/api/<endpoint>` error frames and in `/all` error frames). |
| `await client.close()` | Close the underlying `httpx.AsyncClient` (do **not** call this manually when you pass `owns_client=True` to `supervise_plugin`). |

Recommended `error_type` names:

- `FETCH_FAILED` — HTTP fetch failed (network, timeout, non-2xx)
- `PARSE_FAILED` — Upstream response parsing failed (broken JSON, abnormal structure)
- `PUSH_FAILED` — Push back to the main program failed (main program down / wrong token / internal error) — usually auto-reported by `safe_push`
- `UPSTREAM_TIMEOUT` / `WEBSOCKET_CLOSED` — other scenarios

---

## 4. SDK: `app.plugins._runtime` shared helpers

Reduce boilerplate in every plugin:

| Function | Purpose |
|---|---|
| `path_to_source(path)` | `"/api/cwa"` → `"cwa"`; used for `/all` routing. |
| `await safe_push(client, endpoint, data, *, md5=None)` | Calls `client.push`; on failure, **auto** reports `PUSH_FAILED` and swallows the exception. |
| `await safe_report_error(client, endpoint, error_type, message, details=None)` | Calls `client.report_error`; failures are silent (do not block the loop). |
| `await supervise_plugin(tasks, stop_event, client=None, *, owns_client=False)` | Uses `asyncio.gather(..., return_exceptions=True)` to manage task lifecycles uniformly; on `CancelledError` it sets the stop event and cancels all tasks; with `owns_client=True` it closes the client before returning. |

**Recommended pattern**:

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

## 5. Two common patterns

### A. WebSocket consumption

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
                warned = False  # reset on success — next failure cycle can re-warn
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
                logger.warning("[%s] persistent connection failure: %s", url, exc)
                warned = True
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        delay = min(delay * 2, RECONNECT_MAX_DELAY)
```

### B. HTTP polling

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
        if md5 != last_md5:           # only push when data changed
            last_md5 = md5
            await safe_push(client, endpoint, data)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=POLL_INTERVAL)
        except asyncio.TimeoutError:
            pass
```

---

## 6. Logging conventions

- **Only** `INFO` / `WARNING` / `ERROR` are used
- On startup, emit **one** `INFO` line stating how many connections / endpoints this plugin maintains
- Normal `connecting` / `connected` **must not** log (avoid reconnect-storm spam)
- Persistent connection failure (backoff reaches the cap) — emit **one** `WARNING` per failure cycle, reset on successful reconnect
- Clean upstream disconnects (the `async for` loop ends naturally) are silent — no WARNING
- For business errors use `safe_report_error`; don't use `logger.error` (errors are pushed to WS subscribers)

---

## 7. Documentation

Each endpoint in the generated static docs shows a description + a fields table.

**Priority** (each item judged independently):

```
docs_overrides.yaml (project root)
    > PLUGIN_CONFIG.endpoints[].description / fields
    > PLUGIN_CONFIG.description / fields
```

- In the plugin, only write the **minimal defaults** (so the doc page isn't blank)
- Users **override** via `docs_overrides.yaml` (publish/edit docs without touching code)
- `fields` is **whole-replaced**, not key-merged — this avoids doc/data drift when the override's field definition differs from the default

See the comments at the top of `docs_overrides.yaml` for usage.

---

## 8. Testing conventions

Test files live in `tests/`, named `test_<plugin>_plugin.py`.

You need to replace `PluginClient` with a `_FakeClient` stub:

```python
class _FakeClient:
    def __init__(self):
        self.calls = []     # push records
        self.errors = []    # report_error records

    async def push(self, endpoint, data, source=None, md5=None):
        self.calls.append({"endpoint": endpoint, "data": data, "source": source, "md5": md5})
        return {"ok": True}

    async def report_error(self, endpoint, error_type, message, details=None):
        self.errors.append({"endpoint": endpoint, "error_type": error_type, "message": message, "details": details})
        return {"ok": True}

    async def close(self):
        pass
```

Because the stub is not a real `PluginClient`, calls like `safe_push(fake, ...)` need `# type: ignore[arg-type]` to silence Pylance.

Run tests: `python -m pytest tests/ -q`

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Startup error `DuplicateEndpointError` | path already taken by another plugin | rename; or merge into that plugin |
| Startup error `endpoints 必须是非空 list` | `endpoints` missing or empty | at least 1 entry |
| Startup error `endpoints[0].path 必须以 /api/ 开头` | path missing the prefix | use `/api/xxx` |
| Persistent WARNING in WS logs | upstream unreachable | check network; wrong token also returns 401 |
| Doc page shows empty `fields` for an endpoint | not declared in plugin nor in `docs_overrides.yaml` | fill one of the two |
| Endpoint receives no data | callback token mismatch with `PLUGIN_CONFIG.token` | main program uses the token allocated by `manager.py`; the plugin's `PLUGIN_CONFIG.token` must match |
