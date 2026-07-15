"""插件配置加载与校验。

每个插件是一个 Python 文件，声明一个 PLUGIN_CONFIG 字典，结构：

    PLUGIN_CONFIG = {
        "name": str,           # 必填
        "token": str,          # 必填
        "endpoints": [EndpointDict, ...],   # 必填非空
        "description": str,    # 可选，缺省 ""；可被 per-endpoint description 覆盖
        "fields": dict,        # 可选，缺省 {}；可被 per-endpoint fields 整体替换
    }

endpoint dict 形如：
    {"path": "/api/xxx", "protocols": ["http", "ws"], "exclude_from_all": False}

顶层 ``description`` / ``fields`` 与 ``EndpointSpec.description`` / ``EndpointSpec.fields``
保持一致的"可选 + 回退"语义，docs 渲染时由
:func:`app.docs_gen.generator._resolve_doc` 统一合并。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

VALID_PROTOCOLS = ("http", "ws")


class PluginConfigError(Exception):
    """插件配置错误基类。"""


class DuplicateEndpointError(PluginConfigError):
    """跨插件 endpoint 路径重复。"""


@dataclass(frozen=True)
class EndpointSpec:
    """单个 endpoint 的元数据。

    ``description`` / ``fields`` 是可选的，per-endpoint 独立。
    - ``description``：本端点的语义说明（如"地震速报 / 本地有感地震"）。
    - ``fields``：本端点 Data 负载内字段的描述（{name: {type, nullable, description}}）。
    若未设置，docs 渲染时回退到 plugin 级 ``description`` / ``fields``。
    """

    path: str
    protocols: list[str]
    exclude_from_all: bool = False
    description: str = ""
    fields: dict[str, Any] = field(default_factory=dict)


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
    def _parse_endpoint(
        plugin_path: Path, index: int, ep: Any
    ) -> EndpointSpec:
        """校验并转换单条 endpoint dict → EndpointSpec。"""
        if not isinstance(ep, dict):
            raise PluginConfigError(
                f"插件 {plugin_path} endpoints[{index}] 不是 dict"
            )
        if "path" not in ep or "protocols" not in ep:
            raise PluginConfigError(
                f"插件 {plugin_path} endpoints[{index}] 缺少 path/protocols"
            )
        ep_path = ep["path"]
        protocols = ep["protocols"]
        if not isinstance(ep_path, str) or not ep_path.startswith("/api/"):
            raise PluginConfigError(
                f"插件 {plugin_path} endpoints[{index}].path "
                f"必须以 /api/ 开头: {ep_path!r}"
            )
        if (
            not isinstance(protocols, list)
            or len(protocols) == 0
            or not all(
                isinstance(p, str) and p in VALID_PROTOCOLS for p in protocols
            )
        ):
            raise PluginConfigError(
                f"插件 {plugin_path} endpoints[{index}].protocols 非法: {protocols!r}"
            )
        ep_desc = ep.get("description", "")
        ep_fields_raw = ep.get("fields", {})
        if not isinstance(ep_desc, str):
            raise PluginConfigError(
                f"插件 {plugin_path} endpoints[{index}].description 必须是 str"
            )
        if not isinstance(ep_fields_raw, dict):
            raise PluginConfigError(
                f"插件 {plugin_path} endpoints[{index}].fields 必须是 dict"
            )
        return EndpointSpec(
            path=ep_path,
            protocols=list(protocols),
            exclude_from_all=bool(ep.get("exclude_from_all", False)),
            description=ep_desc,
            fields=dict(ep_fields_raw),
        )

    @staticmethod
    def _parse_endpoints(
        plugin_path: Path, endpoints_raw: Any
    ) -> list[EndpointSpec]:
        """校验并转换 endpoints 列表。"""
        if not isinstance(endpoints_raw, list) or len(endpoints_raw) == 0:
            raise PluginConfigError(
                f"插件 {plugin_path} 的 endpoints 必须是非空 list"
            )
        endpoints: list[EndpointSpec] = []
        seen_paths: set[str] = set()
        for i, ep in enumerate(endpoints_raw):
            spec = PluginLoader._parse_endpoint(plugin_path, i, ep)
            if spec.path in seen_paths:
                raise PluginConfigError(
                    f"插件 {plugin_path} 内部 endpoint 重复: {spec.path}"
                )
            seen_paths.add(spec.path)
            endpoints.append(spec)
        return endpoints

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
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise PluginConfigError(f"执行插件 {path} 失败: {exc}") from exc

        if not hasattr(module, "PLUGIN_CONFIG"):
            raise PluginConfigError(f"插件 {path} 缺少 PLUGIN_CONFIG")

        cfg = module.PLUGIN_CONFIG
        if not isinstance(cfg, dict):
            raise PluginConfigError(f"插件 {path} 的 PLUGIN_CONFIG 必须是 dict")

        required = ("name", "token", "endpoints")
        missing = [k for k in required if k not in cfg]
        if missing:
            raise PluginConfigError(f"插件 {path} 缺少字段: {missing}")

        # description 可选（缺省 ""；可被 per-endpoint description 覆盖）
        description = cfg.get("description", "")
        if not isinstance(description, str):
            raise PluginConfigError(
                f"插件 {path} 的 description 必须是 str"
            )

        # fields 可选（缺省 {}；可被 per-endpoint fields 整体替换）
        fields = cfg.get("fields", {})
        if not isinstance(fields, dict):
            raise PluginConfigError(
                f"插件 {path} 的 fields 必须是 dict"
            )

        endpoints = PluginLoader._parse_endpoints(path, cfg["endpoints"])

        return PluginConfig(
            name=str(cfg["name"]),
            token=str(cfg["token"]),
            description=description,
            fields=dict(fields),
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
