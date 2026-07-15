"""``app.docs_gen.generator`` 合并逻辑单元测试。

覆盖 ``_resolve_doc`` 的三档优先级：
- override > endpoint > plugin
- description / fields 各自独立判定
- 空字符串 / 空 dict 视为"未提供"（走下一档）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.docs_gen.generator import _resolve_doc
from app.docs_gen.overrides import DocsOverrides, EndpointOverride
from app.plugins.config import EndpointSpec, PluginConfig


# ------------------------- helpers -------------------------


def _make_plugin(
    *,
    description: str = "PLUGIN_DESC",
    fields: dict[str, Any] | None = None,
    endpoints: list[EndpointSpec] | None = None,
) -> PluginConfig:
    return PluginConfig(
        name="t",
        token="t",
        description=description,
        fields=fields if fields is not None else {},
        endpoints=endpoints or [],
        file_path=Path("t.py"),
    )


def _ep(
    path: str = "/api/foo",
    *,
    description: str = "",
    fields: dict[str, Any] | None = None,
) -> EndpointSpec:
    return EndpointSpec(
        path=path,
        protocols=["http", "ws"],
        exclude_from_all=False,
        description=description,
        fields=fields if fields is not None else {},
    )


# ------------------------- 优先级：description -------------------------


def test_description_falls_back_to_plugin():
    plugin = _make_plugin(description="PLUGIN-LEVEL")
    ep = _ep()  # 无 description
    desc, _ = _resolve_doc(ep, plugin, override=None)
    assert desc == "PLUGIN-LEVEL"


def test_description_uses_endpoint_when_no_override():
    plugin = _make_plugin(description="PLUGIN-LEVEL")
    ep = _ep(description="EP-LEVEL")
    desc, _ = _resolve_doc(ep, plugin, override=None)
    assert desc == "EP-LEVEL"


def test_description_uses_override_over_endpoint():
    plugin = _make_plugin(description="PLUGIN-LEVEL")
    ep = _ep(description="EP-LEVEL")
    override = EndpointOverride(
        path="/api/foo", description="OVERRIDE", fields={}
    )
    desc, _ = _resolve_doc(ep, plugin, override)
    assert desc == "OVERRIDE"


def test_description_empty_override_falls_back_to_endpoint():
    plugin = _make_plugin(description="PLUGIN-LEVEL")
    ep = _ep(description="EP-LEVEL")
    # override 提供但 description 是空字符串：视为"未提供"
    override = EndpointOverride(path="/api/foo", description="", fields={})
    desc, _ = _resolve_doc(ep, plugin, override)
    assert desc == "EP-LEVEL"


# ------------------------- 优先级：fields -------------------------


def test_fields_falls_back_to_plugin():
    plugin = _make_plugin(fields={"a": {"type": "string"}})
    ep = _ep()  # 无 fields
    _, fields = _resolve_doc(ep, plugin, override=None)
    assert fields == {"a": {"type": "string"}}


def test_fields_uses_endpoint_when_non_empty():
    plugin = _make_plugin(fields={"a": {"type": "string"}})
    ep = _ep(fields={"a": {"type": "number"}})
    _, fields = _resolve_doc(ep, plugin, override=None)
    assert fields == {"a": {"type": "number"}}


def test_fields_empty_endpoint_falls_back_to_plugin():
    plugin = _make_plugin(fields={"a": {"type": "string"}})
    ep = _ep(fields={})
    _, fields = _resolve_doc(ep, plugin, override=None)
    assert fields == {"a": {"type": "string"}}


def test_fields_override_replaces_when_non_empty():
    plugin = _make_plugin(fields={"a": {"type": "string"}})
    ep = _ep(fields={"a": {"type": "number"}})
    override = EndpointOverride(
        path="/api/foo",
        description="",
        fields={"b": {"type": "boolean"}},
    )
    _, fields = _resolve_doc(ep, plugin, override)
    # 整体替换：仅保留 override 的 b
    assert fields == {"b": {"type": "boolean"}}


def test_fields_empty_override_falls_back_to_endpoint():
    plugin = _make_plugin(fields={"a": {"type": "string"}})
    ep = _ep(fields={"a": {"type": "number"}})
    override = EndpointOverride(
        path="/api/foo", description="", fields={}
    )
    _, fields = _resolve_doc(ep, plugin, override)
    assert fields == {"a": {"type": "number"}}


# ------------------------- 独立性：description / fields 各自判定 -------------------------


def test_override_can_provide_description_only():
    """override 只给 description 时，fields 仍走 endpoint。"""
    plugin = _make_plugin(fields={"a": {"type": "string"}})
    ep = _ep(fields={"b": {"type": "number"}})
    override = EndpointOverride(
        path="/api/foo", description="ONLY DESC", fields={}
    )
    desc, fields = _resolve_doc(ep, plugin, override)
    assert desc == "ONLY DESC"
    assert fields == {"b": {"type": "number"}}  # 来自 endpoint


# ------------------------- DocsOverrides 与生成器集成 -------------------------


def test_docs_overrides_lookup_for_path():
    overrides = DocsOverrides(
        {
            "/api/foo": EndpointOverride(
                path="/api/foo", description="X", fields={}
            )
        }
    )
    assert overrides.get("/api/foo") is not None
    assert overrides.get("/api/bar") is None
