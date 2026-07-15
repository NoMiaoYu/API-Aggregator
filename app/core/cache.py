"""内存缓存：只保留每个 endpoint 的最新数据。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CacheEntry:
    """缓存条目，存放下游可见的最新数据快照。"""

    data: Any
    md5: str
    timestamp: int
    source: str


class DataCache:
    """进程内单实例缓存。set 为覆盖式；snapshot 为浅拷贝。"""

    def __init__(self) -> None:
        self._store: dict[str, CacheEntry] = {}

    def set(self, key: str, entry: CacheEntry) -> None:
        """覆盖式写入。"""
        self._store[key] = entry

    def get(self, key: str) -> CacheEntry | None:
        """读取；不存在返回 None。"""
        return self._store.get(key)

    def keys(self) -> list[str]:
        """返回所有 key 列表（拷贝）。"""
        return list(self._store.keys())

    def snapshot(self) -> dict[str, CacheEntry]:
        """返回全部条目的浅拷贝。"""
        return dict(self._store)

    def remove(self, key: str) -> None:
        """删除指定 key；不存在静默。"""
        self._store.pop(key, None)

    def clear(self) -> None:
        """清空全部条目。"""
        self._store.clear()
