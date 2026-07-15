"""``docs_overrides.yaml`` 加载器。

为所有插件的 endpoint 提供**用户可编辑**的 description / fields 覆盖。
文件不存在时返回空覆盖（使用插件内置默认）。

文件格式（YAML）：

.. code-block:: yaml

    endpoints:
      /api/my-source:
        description: |
          详细说明（多行）。
        fields:
          lat:
            type: number
            nullable: false
            description: "震中纬度（°N 为正）"
          lon:
            ...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class DocsOverridesError(Exception):
    """docs_overrides.yaml 解析错误（仅用于"文件级"硬错误：读不到 / 顶层非 dict /
    endpoints 节点非 dict / 单条 entry 内部字段类型错）。单条 entry 的 key 非法
    走"记录 WARNING 后跳过"路径，不抛错。
    """


@dataclass(frozen=True)
class EndpointOverride:
    """单个 endpoint 的用户覆盖。

    - ``description``：空字符串表示"未提供"（fallback 到 plugin）。
    - ``fields``：空 dict 表示"未提供"（fallback 到 plugin）。
    """

    path: str
    description: str = ""
    fields: dict[str, Any] = field(default_factory=dict)


class DocsOverrides:
    """``docs_overrides.yaml`` 内容的只读视图。"""

    def __init__(
        self, by_path: dict[str, EndpointOverride] | None = None
    ) -> None:
        self._by_path: dict[str, EndpointOverride] = dict(by_path or {})

    # ---------- 构造 ----------

    @classmethod
    def load(cls, path: str | Path) -> "DocsOverrides":
        """从 YAML 文件加载。

        - 文件不存在：返回空覆盖（不抛错，调用方可继续用 plugin 默认）。
        - 文件存在但格式错误：抛 ``DocsOverridesError``。
        """
        path = Path(path)
        if not path.is_file():
            return cls()

        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            raise DocsOverridesError(
                f"读取 {path} 失败: {exc}"
            ) from exc

        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise DocsOverridesError(
                f"{path}: 顶层必须是 dict，实际 {type(data).__name__}"
            )

        endpoints_raw = data.get("endpoints", {})
        if not isinstance(endpoints_raw, dict):
            raise DocsOverridesError(
                f"{path}: endpoints 必须是 dict，实际 {type(endpoints_raw).__name__}"
            )

        by_path: dict[str, EndpointOverride] = {}
        for ep_path, ep_data in endpoints_raw.items():
            key = cls._validate_key(path, ep_path)
            if key is None:
                # 非法 key 已记 WARNING；不阻塞同文件其他合法 entry
                continue
            by_path[key] = cls._parse_entry(path, key, ep_data)
        return cls(by_path)

    @staticmethod
    def _validate_key(file: Path, key: Any) -> str | None:
        """校验 endpoint key：合法返回 key，非法返回 ``None``（不抛错）。

        非法 key 仅记 WARNING 并由 ``load`` 跳过——同一文件里的其他合法
        endpoint 不受影响。
        """
        if not isinstance(key, str) or not key.startswith("/api/"):
            logger.warning(
                "[%s] 忽略非法 endpoint key: %r（必须是 '/api/...' 字符串）",
                file, key,
            )
            return None
        return key

    @classmethod
    def _parse_entry(
        cls, file: Path, key: str, value: Any
    ) -> EndpointOverride:
        if not isinstance(value, dict):
            raise DocsOverridesError(
                f"{file}: endpoint {key} 必须是 dict"
            )
        desc_raw = value.get("description", "")
        if desc_raw is None:
            desc_raw = ""
        if not isinstance(desc_raw, str):
            raise DocsOverridesError(
                f"{file}: endpoint {key}.description 必须是 str"
            )
        fields_raw = value.get("fields", {})
        if fields_raw is None:
            fields_raw = {}
        if not isinstance(fields_raw, dict):
            raise DocsOverridesError(
                f"{file}: endpoint {key}.fields 必须是 dict"
            )
        return EndpointOverride(
            path=key, description=desc_raw, fields=dict(fields_raw)
        )

    # ---------- 查询 ----------

    def get(self, path: str) -> EndpointOverride | None:
        """按 endpoint path 查询覆盖；未命中返回 ``None``。"""
        return self._by_path.get(path)

    def is_empty(self) -> bool:
        return not self._by_path

    def paths(self) -> list[str]:
        return sorted(self._by_path)

    def __len__(self) -> int:
        return len(self._by_path)

    def __contains__(self, path: object) -> bool:
        return isinstance(path, str) and path in self._by_path
