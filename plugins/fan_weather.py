"""多端点 PoC 插件：fan_weather。

演示：一个插件文件提供两个 endpoint（/api/weather 和 /api/weather_alert），
插件内部只维护一条"上游连接"（这里用本地定时器模拟），收到消息后 fan-out
推回主程序。

手动验证：
    1. python server.py
    2. curl http://127.0.0.1:8000/api/weather           # 200 + Data
    3. curl http://127.0.0.1:8000/api/weather_alert     # 405
    4. websocat ws://127.0.0.1:8000/ws/weather          # 收到 snapshot / update
    5. websocat ws://127.0.0.1:8000/ws/weather_alert    # 收到 snapshot / update
    6. websocat ws://127.0.0.1:8000/ws/all              # initial_all 只含 /api/weather
"""

from __future__ import annotations

import asyncio
import random
import sys
import time
from typing import Any

from app.plugins.client import PluginClient, compute_md5

PLUGIN_CONFIG = {
    "name": "fan_weather",
    "token": "demo-token-change-me",  # 实际项目应来自环境变量
    "description": "PoC 多端点插件：演示一个子进程维护 1 条上游连接，fan-out 到 2 个 endpoint",
    "endpoints": [
        {
            "path": "/api/weather",
            "protocols": ["http", "ws"],
            "exclude_from_all": False,
        },
        {
            "path": "/api/weather_alert",
            "protocols": ["ws"],
            "exclude_from_all": True,
        },
    ],
    "fields": {
        "temp": {"type": "number", "description": "温度（℃）", "nullable": False},
        "humidity": {"type": "number", "description": "湿度（%）", "nullable": False},
        "city": {"type": "string", "description": "城市", "nullable": False},
    },
}

# 插件内常量：fan-out 目标
ENDPOINT_WEATHER = "/api/weather"
ENDPOINT_ALERT = "/api/weather_alert"
SOURCE = "fan_weather"

# 定时器周期（秒）
TICK_INTERVAL = 3.0


def _generate_weather() -> dict[str, Any]:
    return {
        "temp": round(random.uniform(15, 30), 1),
        "humidity": round(random.uniform(40, 80), 1),
        "city": "上海",
    }


def _generate_alert() -> dict[str, Any] | None:
    """偶尔生成告警，模拟上游突发事件。"""
    if random.random() < 0.3:
        return {
            "level": random.choice(["yellow", "orange", "red"]),
            "message": random.choice(["雷暴", "大风", "高温"]),
            "issued_at": int(time.time()),
        }
    return None


async def _push_safely(
    client: PluginClient, endpoint: str, payload: dict[str, Any]
) -> None:
    """单点失败不影响其他 endpoint。"""
    try:
        await client.push(
            endpoint, payload, source=SOURCE, md5=compute_md5(payload)
        )
    except Exception as exc:  # noqa: BLE001
        try:
            await client.report_error(
                endpoint, "PUSH_FAILED", str(exc), {"payload_keys": list(payload.keys())}
            )
        except Exception:
            pass


async def _fanout(
    client: PluginClient,
    weather: dict[str, Any],
    alert: dict[str, Any] | None,
) -> None:
    """把同一条"上游消息"拆成多个 endpoint 推送。"""
    tasks = [_push_safely(client, ENDPOINT_WEATHER, weather)]
    if alert is not None:
        tasks.append(_push_safely(client, ENDPOINT_ALERT, alert))
    await asyncio.gather(*tasks, return_exceptions=True)


async def start(callback_url: str, token: str) -> None:
    """插件入口。"""
    client = PluginClient(callback_url, token)
    try:
        while True:
            weather = _generate_weather()
            alert = _generate_alert()
            await _fanout(client, weather, alert)
            await asyncio.sleep(TICK_INTERVAL)
    except asyncio.CancelledError:
        pass
    finally:
        await client.close()


if __name__ == "__main__":
    # 由 PluginManager 作为子进程调用：
    #   python plugins/fan_weather.py <callback_url> <token>
    callback_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    token = sys.argv[2] if len(sys.argv) > 2 else PLUGIN_CONFIG["token"]
    asyncio.run(start(callback_url, token))
