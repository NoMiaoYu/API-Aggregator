"""PluginConfig / PluginLoader 单元测试。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from app.plugins.config import (
    DuplicateEndpointError,
    EndpointSpec,
    PluginConfig,
    PluginConfigError,
    PluginLoader,
)


# ------------------------- helpers -------------------------


def _write_plugin(path: Path, body: str) -> Path:
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _valid_config(name: str = "fan_weather", path: str = "/api/foo", extra: str = "") -> str:
    return f'''
        PLUGIN_CONFIG = {{
            "name": "{name}",
            "token": "tok-{name}",
            "description": "desc",
            "endpoints": [
                {{
                    "path": "{path}",
                    "protocols": ["http", "ws"],
                }},
            ],
            "fields": {{
                "temp": {{"type": "number", "description": "温度", "nullable": False}},
            }},
        }}
        {extra}
    '''


# ------------------------- load_plugin -------------------------


def test_load_plugin_returns_plugin_config(tmp_path: Path):
    p = _write_plugin(tmp_path / "p.py", _valid_config())
    cfg = PluginLoader.load_plugin(p)
    assert isinstance(cfg, PluginConfig)
    assert cfg.name == "fan_weather"
    assert cfg.token == "tok-fan_weather"
    assert cfg.description == "desc"
    assert cfg.file_path == p
    assert len(cfg.endpoints) == 1
    assert isinstance(cfg.endpoints[0], EndpointSpec)
    assert cfg.endpoints[0].path == "/api/foo"
    assert cfg.endpoints[0].protocols == ["http", "ws"]
    assert cfg.endpoints[0].exclude_from_all is False
    assert cfg.fields == {
        "temp": {"type": "number", "description": "温度", "nullable": False}
    }


def test_source_derived_from_name(tmp_path: Path):
    p = _write_plugin(tmp_path / "p.py", _valid_config("fan_weather"))
    cfg = PluginLoader.load_plugin(p)
    assert cfg.source == "fan_weather"


def test_exclude_from_all_defaults_false(tmp_path: Path):
    p = _write_plugin(tmp_path / "p.py", _valid_config())
    cfg = PluginLoader.load_plugin(p)
    assert cfg.endpoints[0].exclude_from_all is False


def test_exclude_from_all_can_be_true(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "n",
            "token": "t",
            "description": "d",
            "endpoints": [
                {"path": "/api/a", "protocols": ["ws"], "exclude_from_all": True},
            ],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    cfg = PluginLoader.load_plugin(p)
    assert cfg.endpoints[0].exclude_from_all is True


def test_plugin_config_missing_module_attribute(tmp_path: Path):
    p = _write_plugin(tmp_path / "p.py", "X = 1\n")
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_plugin_config_missing_name(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "token": "t", "description": "d",
            "endpoints": [{"path": "/api/a", "protocols": ["http"]}],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_plugin_config_missing_token(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "description": "d",
            "endpoints": [{"path": "/api/a", "protocols": ["http"]}],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_plugin_config_missing_description_defaults_to_empty(tmp_path: Path):
    """description 可选；缺省时 PluginConfig.description = ""。"""
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t",
            "endpoints": [{"path": "/api/a", "protocols": ["http"]}],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    cfg = PluginLoader.load_plugin(p)
    assert cfg.description == ""


def test_plugin_config_missing_fields_defaults_to_empty_dict(tmp_path: Path):
    """fields 可选；缺省时 PluginConfig.fields = {}。"""
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d",
            "endpoints": [{"path": "/api/a", "protocols": ["http"]}],
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    cfg = PluginLoader.load_plugin(p)
    assert cfg.fields == {}


def test_plugin_config_description_wrong_type_raises(tmp_path: Path):
    """description 类型非 str 仍需报错。"""
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": 123,
            "endpoints": [{"path": "/api/a", "protocols": ["http"]}],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_plugin_config_fields_wrong_type_raises(tmp_path: Path):
    """fields 类型非 dict 仍需报错。"""
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d",
            "endpoints": [{"path": "/api/a", "protocols": ["http"]}],
            "fields": "oops",
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_plugin_config_both_description_and_fields_optional(tmp_path: Path):
    """description + fields 同时省略也能成功加载（最小化配置）。"""
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t",
            "endpoints": [{"path": "/api/a", "protocols": ["http"]}],
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    cfg = PluginLoader.load_plugin(p)
    assert cfg.description == ""
    assert cfg.fields == {}


def test_plugin_config_missing_endpoints(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d", "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_plugin_config_empty_endpoints(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d",
            "endpoints": [], "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_endpoint_path_must_start_with_api_prefix(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d",
            "endpoints": [{"path": "/foo", "protocols": ["http"]}],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_endpoint_protocols_must_be_nonempty_list(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d",
            "endpoints": [{"path": "/api/a", "protocols": []}],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_endpoint_protocols_must_contain_valid_values(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d",
            "endpoints": [{"path": "/api/a", "protocols": ["ftp"]}],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_endpoint_path_must_be_unique_within_plugin(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "n", "token": "t", "description": "d",
            "endpoints": [
                {"path": "/api/a", "protocols": ["http"]},
                {"path": "/api/a", "protocols": ["ws"]},
            ],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    with pytest.raises(PluginConfigError):
        PluginLoader.load_plugin(p)


def test_multiple_endpoints_allowed(tmp_path: Path):
    body = '''
        PLUGIN_CONFIG = {
            "name": "fan_weather", "token": "t", "description": "d",
            "endpoints": [
                {"path": "/api/weather", "protocols": ["http", "ws"]},
                {"path": "/api/weather_alert", "protocols": ["ws"], "exclude_from_all": True},
            ],
            "fields": {},
        }
    '''
    p = _write_plugin(tmp_path / "p.py", body)
    cfg = PluginLoader.load_plugin(p)
    assert [e.path for e in cfg.endpoints] == ["/api/weather", "/api/weather_alert"]


# ------------------------- scan_directory -------------------------


def test_scan_directory_loads_all_plugins_sorted(tmp_path: Path):
    _write_plugin(tmp_path / "b_plugin.py", _valid_config("b", path="/api/b"))
    _write_plugin(tmp_path / "a_plugin.py", _valid_config("a", path="/api/a"))
    plugins = PluginLoader.scan_directory(tmp_path)
    assert [p.name for p in plugins] == ["a", "b"]


def test_scan_directory_duplicate_endpoint_raises(tmp_path: Path):
    _write_plugin(tmp_path / "a.py", _valid_config("a"))
    body = _valid_config("b").replace(
        '"/api/foo"', '"/api/foo"'
    ).replace("a_plugin", "b_plugin")  # ensure path collision
    body = f'''
        PLUGIN_CONFIG = {{
            "name": "b", "token": "t-b", "description": "d",
            "endpoints": [
                {{"path": "/api/foo", "protocols": ["http"]}},
            ],
            "fields": {{}},
        }}
    '''
    _write_plugin(tmp_path / "b.py", body)
    with pytest.raises(DuplicateEndpointError):
        PluginLoader.scan_directory(tmp_path)


def test_scan_directory_missing_dir_raises(tmp_path: Path):
    with pytest.raises(PluginConfigError):
        PluginLoader.scan_directory(tmp_path / "no_such_dir")


def test_scan_directory_ignores_non_py_files(tmp_path: Path):
    _write_plugin(tmp_path / "a.py", _valid_config("a"))
    (tmp_path / "README.md").write_text("not a plugin")
    (tmp_path / "b.txt").write_text("not a plugin")
    plugins = PluginLoader.scan_directory(tmp_path)
    assert len(plugins) == 1


def test_duplicate_endpoint_error_is_plugin_config_error():
    assert issubclass(DuplicateEndpointError, PluginConfigError)
