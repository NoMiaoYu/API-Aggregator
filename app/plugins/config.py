"""插件配置加载与校验。

每个插件是一个 Python 文件，声明一个 PLUGIN_CONFIG 字典，结构：

    PLUGIN_CONFIG = {
        "name": str,
        "token": str,
        "description": str,
        "endpoints": [EndpointDict, ...],   # 必填非空
        "fields": {field_name: FieldMeta, ...},
    }

endpoint dict 形如：
    {"path": "/api/xxx", "protocols": ["http", "ws"], "exclude_from_all": False}
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_PROTOCOLS = ("http", "ws")


class PluginConfigError(Exception):
    """插件配置错误基类。"""


class DuplicateEndpointError(PluginConfigError):
    """跨插件 endpoint 路径重复。"""


@dataclass(frozen=True)
class EndpointSpec:
    """单个 endpoint 的元数据。"""

    path: str
    protocols: list[str]
    exclude_from_all: bool = False


@dataclass
class PluginConfig:
    """加载后的插件配置。"""

    name: str
    token: str
    description: str
    fields: dict[str, Any]
    endpoints: list[EndpointSpec]
    file_path: Path
    source: str = ""

    def __post_init__(self) -> None:
        # source 由 name 推导。这是数据在 /all JSON 中使用的顶层 key。
        if not self.source:
            self.source = self.name

    # 方便反查：给定 endpoint path 找出对应 spec
    def find_endpoint(self, path: str) -> EndpointSpec | None:
        for ep in self.endpoints:
            if ep.path == path:
                return ep
        return None


class PluginLoader:
    """从单个 .py 文件或整个目录加载插件配置。"""

    @staticmethod
    def load_plugin(path: str | Path) -> PluginConfig:
        path = Path(path)
        if not path.is_file():
            raise PluginConfigError(f"插件文件不存在: {path}")

        spec = importlib.util.spec_from_file_location(f"_plugin_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise PluginConfigError(f"无法加载插件模块: {path}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            raise PluginConfigError(f"执行插件 {path} 失败: {exc}") from exc

        if not hasattr(module, "PLUGIN_CONFIG"):
            raise PluginConfigError(f"插件 {path} 缺少 PLUGIN_CONFIG")

        cfg = module.PLUGIN_CONFIG
        if not isinstance(cfg, dict):
            raise PluginConfigError(f"插件 {path} 的 PLUGIN_CONFIG 必须是 dict")

        # 顶层必填字段
        required = ("name", "token", "description", "fields", "endpoints")
        missing = [k for k in required if k not in cfg]
        if missing:
            raise PluginConfigError(f"插件 {path} 缺少字段: {missing}")

        endpoints_raw = cfg["endpoints"]
        if not isinstance(endpoints_raw, list) or len(endpoints_raw) == 0:
            raise PluginConfigError(f"插件 {path} 的 endpoints 必须是非空 list")

        endpoints: list[EndpointSpec] = []
        seen_paths: set[str] = set()
        for i, ep in enumerate(endpoints_raw):
            if not isinstance(ep, dict):
                raise PluginConfigError(f"插件 {path} endpoints[{i}] 不是 dict")
            if "path" not in ep or "protocols" not in ep:
                raise PluginConfigError(
                    f"插件 {path} endpoints[{i}] 缺少 path/protocols"
                )
            ep_path = ep["path"]
            protocols = ep["protocols"]
            if not isinstance(ep_path, str) or not ep_path.startswith("/api/"):
                raise PluginConfigError(
                    f"插件 {path} endpoints[{i}].path 必须以 /api/ 开头: {ep_path!r}"
                )
            if (
                not isinstance(protocols, list)
                or len(protocols) == 0
                or not all(isinstance(p, str) and p in VALID_PROTOCOLS for p in protocols)
            ):
                raise PluginConfigError(
                    f"插件 {path} endpoints[{i}].protocols 非法: {protocols!r}"
                )
            if ep_path in seen_paths:
                raise PluginConfigError(
                    f"插件 {path} 内部 endpoint 重复: {ep_path}"
                )
            seen_paths.add(ep_path)
            endpoints.append(
                EndpointSpec(
                    path=ep_path,
                    protocols=list(protocols),
                    exclude_from_all=bool(ep.get("exclude_from_all", False)),
                )
            )

        if not isinstance(cfg["fields"], dict):
            raise PluginConfigError(f"插件 {path} 的 fields 必须是 dict")

        return PluginConfig(
            name=str(cfg["name"]),
            token=str(cfg["token"]),
            description=str(cfg["description"]),
            fields=dict(cfg["fields"]),
            endpoints=endpoints,
            file_path=path,
        )

    @staticmethod
    def scan_directory(directory: str | Path) -> list[PluginConfig]:
        directory = Path(directory)
        if not directory.is_dir():
            raise PluginConfigError(f"插件目录不存在: {directory}")

        py_files = sorted(p for p in directory.glob("*.py") if p.is_file())
        plugins: list[PluginConfig] = []
        seen_paths: dict[str, str] = {}  # path -> plugin name
        for path in py_files:
            cfg = PluginLoader.load_plugin(path)
            for ep in cfg.endpoints:
                if ep.path in seen_paths:
                    raise DuplicateEndpointError(
                        f"endpoint {ep.path} 重复: {seen_paths[ep.path]} 与 {cfg.name}"
                    )
                seen_paths[ep.path] = cfg.name
            plugins.append(cfg)
        return plugins
