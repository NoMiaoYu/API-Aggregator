"""``app.core.config`` 加载器单元测试。

覆盖：
- 默认值（无文件 / 空文件 / 全空 dict）
- 各 section 字段正确加载
- 缺字段时回退到 dataclass 默认
- 错误：顶层非 dict / YAML 解析失败 / language 非法值
- helpers：is_zh / is_en
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import (
    AppConfig,
    ConfigError,
    ServerConfig,
)


# ------------------------- file missing -------------------------


def test_load_missing_file_returns_defaults(tmp_path: Path):
    """配置文件不存在：返回全默认 AppConfig。"""
    cfg = AppConfig.load(tmp_path / "nope.yaml")
    assert cfg.language == "en"
    assert cfg.server.host == "127.0.0.1"
    assert cfg.server.port == 8000
    assert cfg.server.log_level == "INFO"
    assert cfg.callback.base_path == "/api/internal/callback"
    assert cfg.plugins.directory == "plugins"
    assert cfg.plugins.start_timeout == 30
    assert cfg.plugins.max_restarts == 5
    assert cfg.docs.output_dir == "docs"
    assert cfg.docs.overrides_file == "docs_overrides.yaml"


# ------------------------- empty / blank file -------------------------


def test_load_empty_file_returns_defaults(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    assert AppConfig.load(p).language == "en"


def test_load_null_yaml_returns_defaults(tmp_path: Path):
    p = tmp_path / "null.yaml"
    p.write_text("~\n", encoding="utf-8")  # YAML null
    assert AppConfig.load(p).language == "en"


# ------------------------- full file -------------------------


def test_load_full_config(tmp_path: Path):
    p = tmp_path / "cfg.yaml"
    p.write_text(
        """
language: zh
server:
  host: 0.0.0.0
  port: 9000
  log_level: WARNING
callback:
  base_path: /api/internal/cb
plugins:
  directory: my-plugins
  start_timeout: 60
  max_restarts: 10
docs:
  output_dir: build/docs
  overrides_file: my_overrides.yaml
""",
        encoding="utf-8",
    )
    cfg = AppConfig.load(p)
    assert cfg.language == "zh"
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 9000
    assert cfg.server.log_level == "WARNING"
    assert cfg.callback.base_path == "/api/internal/cb"
    assert cfg.plugins.directory == "my-plugins"
    assert cfg.plugins.start_timeout == 60
    assert cfg.plugins.max_restarts == 10
    assert cfg.docs.output_dir == "build/docs"
    assert cfg.docs.overrides_file == "my_overrides.yaml"


def test_load_partial_config_uses_defaults_for_missing_sections(
    tmp_path: Path,
):
    """只填 language，其他 section 全部走默认。"""
    p = tmp_path / "partial.yaml"
    p.write_text("language: zh\n", encoding="utf-8")
    cfg = AppConfig.load(p)
    assert cfg.language == "zh"
    assert cfg.server == ServerConfig()      # 全默认
    assert cfg.plugins.directory == "plugins"


def test_load_partial_section_uses_defaults_for_missing_fields(
    tmp_path: Path,
):
    """只填 server.host，server 其他字段走默认。"""
    p = tmp_path / "partial.yaml"
    p.write_text("server:\n  host: 10.0.0.1\n", encoding="utf-8")
    cfg = AppConfig.load(p)
    assert cfg.server.host == "10.0.0.1"
    assert cfg.server.port == 8000           # 默认
    assert cfg.server.log_level == "INFO"    # 默认


# ------------------------- invalid language -------------------------


def test_load_invalid_language_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("language: fr\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="language"):
        AppConfig.load(p)


def test_load_non_string_language_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("language: 123\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="language 必须是 str"):
        AppConfig.load(p)


# ------------------------- malformed file -------------------------


def test_load_top_level_non_dict_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("- item1\n- item2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="顶层必须是 dict"):
        AppConfig.load(p)


def test_load_invalid_yaml_raises(tmp_path: Path):
    p = tmp_path / "bad.yaml"
    p.write_text("server: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="读取"):
        AppConfig.load(p)


def test_load_unknown_section_field_silently_ignored(tmp_path: Path):
    """Section 内额外字段被忽略，合法字段正常加载。

    设计：YAML 配置文件经常会留下注释掉的 / 旧的 / 试验性的字段，
    加载期宽容处理对演进式配置更友好。
    """
    p = tmp_path / "extra.yaml"
    p.write_text(
        """
server:
  host: 0.0.0.0
  port: 9000
  legacy_setting: true   # 已被 dataclass 移除
  experimental: 42       # 未实现
""",
        encoding="utf-8",
    )
    cfg = AppConfig.load(p)
    assert cfg.server.host == "0.0.0.0"
    assert cfg.server.port == 9000
    # 未声明字段不能在 dataclass 上访问到
    assert not hasattr(cfg.server, "legacy_setting")
    assert not hasattr(cfg.server, "experimental")


def test_load_known_field_with_wrong_type_does_not_raise(tmp_path: Path):
    """已知字段类型错：dataclass 不做运行时类型检查，错误在使用时才暴露。

    文档契约：配置层的 type 检查责任在调用方，AppConfig 只负责字段白名单。
    真实业务代码如 ``cfg.server.port + 1`` 会抛 TypeError；此处只验证加载期
    不抛。
    """
    p = tmp_path / "bad.yaml"
    p.write_text("server:\n  port: 'not-a-number'\n", encoding="utf-8")
    cfg = AppConfig.load(p)
    assert cfg.server.port == "not-a-number"  # 原样存储


def test_load_unknown_field_in_each_section_is_ignored(tmp_path: Path):
    """所有 4 个 section 的未知字段都应被忽略。"""
    p = tmp_path / "extra.yaml"
    p.write_text(
        """
server:
  host: 1.1.1.1
  old: x
callback:
  base_path: /cb
  foo: bar
plugins:
  directory: p
  bogus: 1
  future: 2
docs:
  output_dir: d
  removed: y
""",
        encoding="utf-8",
    )
    cfg = AppConfig.load(p)
    assert cfg.server.host == "1.1.1.1"
    assert cfg.callback.base_path == "/cb"
    assert cfg.plugins.directory == "p"
    assert cfg.docs.output_dir == "d"


def test_load_mixes_known_and_unknown_at_section_level(tmp_path: Path):
    """同 section 混合已知 + 未知字段，已知字段必须被实例化。"""
    p = tmp_path / "mix.yaml"
    p.write_text(
        """
plugins:
  directory: my-plugins
  start_timeout: 60
  extra_junk: keep-me-out
  more: 1
""",
        encoding="utf-8",
    )
    cfg = AppConfig.load(p)
    assert cfg.plugins.directory == "my-plugins"
    assert cfg.plugins.start_timeout == 60
    assert cfg.plugins.max_restarts == 5  # 默认


# ------------------------- from_dict -------------------------


def test_from_dict_minimal():
    cfg = AppConfig.from_dict({})
    assert cfg.language == "en"


def test_from_dict_with_sections():
    cfg = AppConfig.from_dict(
        {
            "language": "en",
            "server": {"port": 1234},
            "plugins": {"directory": "x"},
        }
    )
    assert cfg.server.port == 1234
    assert cfg.server.host == "127.0.0.1"  # 默认
    assert cfg.plugins.directory == "x"
    assert cfg.callback.base_path == "/api/internal/callback"  # 默认


# ------------------------- helpers -------------------------


def test_is_zh_and_is_en(tmp_path: Path):
    p_zh = tmp_path / "zh.yaml"
    p_zh.write_text("language: zh\n", encoding="utf-8")
    cfg_zh = AppConfig.load(p_zh)
    assert cfg_zh.is_zh is True
    assert cfg_zh.is_en is False

    cfg_en = AppConfig()
    assert cfg_en.is_zh is False
    assert cfg_en.is_en is True


# ------------------------- frozen -------------------------


def test_app_config_is_frozen():
    """AppConfig 是 frozen，运行时不能改字段。"""
    cfg = AppConfig()
    with pytest.raises(Exception):  # FrozenInstanceError
        cfg.language = "zh"  # type: ignore[misc]
