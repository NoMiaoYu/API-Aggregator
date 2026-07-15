"""``app.docs_gen.overrides`` 加载器单元测试。

覆盖：
- ``load``：文件不存在 / 有效 YAML / 非法类型 / 顶层非 dict
- ``get``：命中 / 未命中
- 校验：endpoint key 必须是 ``/api/...`` 字符串
- 校验：endpoint value 必须是 dict
- 校验：description / fields 类型
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.docs_gen.overrides import (
    DocsOverrides,
    DocsOverridesError,
    EndpointOverride,
)


# ------------------------- 文件不存在 -------------------------


def test_load_missing_file_returns_empty(tmp_path: Path):
    path = tmp_path / "missing.yaml"
    overrides = DocsOverrides.load(path)
    assert overrides.is_empty()
    assert overrides.get("/api/foo") is None


# ------------------------- 有效 YAML -------------------------


def test_load_valid_yaml_parses_all_endpoints(tmp_path: Path):
    yaml_path = tmp_path / "docs_overrides.yaml"
    yaml_path.write_text(
        """
endpoints:
  /api/hko-quake:
    description: |
      地震速报。
    fields:
      lat:
        type: number
        nullable: false
        description: "震中纬度"
  /api/hko-felt:
    description: |
      本地有感。
    fields:
      mag:
        type: number
        nullable: false
        description: "芮氏规模"
""",
        encoding="utf-8",
    )
    overrides = DocsOverrides.load(yaml_path)
    assert len(overrides) == 2

    quake = overrides.get("/api/hko-quake")
    assert isinstance(quake, EndpointOverride)
    assert "地震速报" in quake.description
    assert quake.fields["lat"]["description"] == "震中纬度"

    felt = overrides.get("/api/hko-felt")
    assert felt is not None
    assert felt.fields["mag"]["type"] == "number"


def test_load_empty_endpoints_returns_empty(tmp_path: Path):
    yaml_path = tmp_path / "empty.yaml"
    yaml_path.write_text("endpoints: {}\n", encoding="utf-8")
    overrides = DocsOverrides.load(yaml_path)
    assert overrides.is_empty()


def test_load_yaml_without_endpoints_key(tmp_path: Path):
    yaml_path = tmp_path / "no_ep.yaml"
    yaml_path.write_text("something_else: 1\n", encoding="utf-8")
    overrides = DocsOverrides.load(yaml_path)
    assert overrides.is_empty()


# ------------------------- 校验：endpoint key 必须是 /api/... 字符串 -------------------------


def test_load_rejects_non_string_key(tmp_path: Path):
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("endpoints:\n  123: {}\n", encoding="utf-8")
    with pytest.raises(DocsOverridesError, match="endpoint key"):
        DocsOverrides.load(yaml_path)


def test_load_rejects_key_without_api_prefix(tmp_path: Path):
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("endpoints:\n  /foo: {}\n", encoding="utf-8")
    with pytest.raises(DocsOverridesError, match="/api/"):
        DocsOverrides.load(yaml_path)


# ------------------------- 校验：endpoint value 必须是 dict -------------------------


def test_load_rejects_non_dict_endpoint_value(tmp_path: Path):
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("endpoints:\n  /api/foo: just-a-string\n",
                        encoding="utf-8")
    with pytest.raises(DocsOverridesError, match="必须是 dict"):
        DocsOverrides.load(yaml_path)


# ------------------------- 校验：description / fields 类型 -------------------------


def test_load_rejects_non_string_description(tmp_path: Path):
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text(
        "endpoints:\n  /api/foo:\n    description: 123\n",
        encoding="utf-8",
    )
    with pytest.raises(DocsOverridesError, match="description 必须是 str"):
        DocsOverrides.load(yaml_path)


def test_load_rejects_non_dict_fields(tmp_path: Path):
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text(
        "endpoints:\n  /api/foo:\n    fields: not-a-dict\n",
        encoding="utf-8",
    )
    with pytest.raises(DocsOverridesError, match="fields 必须是 dict"):
        DocsOverrides.load(yaml_path)


# ------------------------- 校验：顶层 / endpoints 类型 -------------------------


def test_load_rejects_top_level_non_dict(tmp_path: Path):
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(DocsOverridesError, match="顶层必须是 dict"):
        DocsOverrides.load(yaml_path)


def test_load_rejects_endpoints_non_dict(tmp_path: Path):
    yaml_path = tmp_path / "bad.yaml"
    yaml_path.write_text("endpoints:\n  - /api/foo\n", encoding="utf-8")
    with pytest.raises(DocsOverridesError, match="endpoints 必须是 dict"):
        DocsOverrides.load(yaml_path)


# ------------------------- 边界：description / fields 可省略 -------------------------


def test_load_endpoint_with_no_description_or_fields(tmp_path: Path):
    yaml_path = tmp_path / "ok.yaml"
    yaml_path.write_text("endpoints:\n  /api/foo: {}\n", encoding="utf-8")
    overrides = DocsOverrides.load(yaml_path)
    ep = overrides.get("/api/foo")
    assert ep is not None
    assert ep.description == ""
    assert ep.fields == {}


# ------------------------- 查询辅助 -------------------------


def test_paths_returns_sorted_paths(tmp_path: Path):
    yaml_path = tmp_path / "ok.yaml"
    yaml_path.write_text(
        "endpoints:\n"
        "  /api/z: {}\n"
        "  /api/a: {}\n"
        "  /api/m: {}\n",
        encoding="utf-8",
    )
    overrides = DocsOverrides.load(yaml_path)
    assert overrides.paths() == ["/api/a", "/api/m", "/api/z"]


def test_contains_operator(tmp_path: Path):
    yaml_path = tmp_path / "ok.yaml"
    yaml_path.write_text(
        "endpoints:\n  /api/foo: {}\n", encoding="utf-8"
    )
    overrides = DocsOverrides.load(yaml_path)
    assert "/api/foo" in overrides
    assert "/api/bar" not in overrides
