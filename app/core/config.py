"""Global project configuration loader.

Reads the project-root ``config.yaml`` and exposes a strongly-typed
``AppConfig`` dataclass.

Design
------
- Missing file → return defaults (no error; useful for fresh checkouts).
- Top-level non-dict / YAML parse error → raise :class:`ConfigError`.
- Missing keys → use dataclass defaults (no need to fill every field).
- Unknown language → raise :class:`ConfigError` (validates only the
  language field; other fields accept any str / int as documented).

Usage
-----
::

    from app.core.config import AppConfig

    cfg = AppConfig.load("config.yaml")
    print(cfg.language)       # "en" or "zh"
    print(cfg.server.port)    # 8000
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

# Allowed values for the ``language`` field.
Language = Literal["en", "zh"]
ALLOWED_LANGUAGES: tuple[str, ...] = ("en", "zh")


class ConfigError(Exception):
    """Raised when ``config.yaml`` is malformed or has an invalid value."""


# ---------------------------------------------------------------------------
# Section dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerConfig:
    """Main HTTP/WS server settings."""

    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"


@dataclass(frozen=True)
class CallbackConfig:
    """Internal callback endpoint plugins use to push data back."""

    base_path: str = "/api/internal/callback"


@dataclass(frozen=True)
class PluginsConfig:
    """Plugin loader / lifecycle settings."""

    directory: str = "plugins"
    start_timeout: int = 30
    max_restarts: int = 5


@dataclass(frozen=True)
class DocsConfig:
    """Static documentation generation settings."""

    output_dir: str = "docs"
    overrides_file: str = "docs_overrides.yaml"


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

# Pre-compute valid-field sets so ``from_dict`` can filter out unknown YAML
# keys without instantiating ``fields()`` on every load. Keeping these
# module-level (instead of per-call) avoids 4× repeated dataclass inspection.
_SERVER_FIELDS = {f.name for f in fields(ServerConfig)}
_CALLBACK_FIELDS = {f.name for f in fields(CallbackConfig)}
_PLUGINS_FIELDS = {f.name for f in fields(PluginsConfig)}
_DOCS_FIELDS = {f.name for f in fields(DocsConfig)}


def _pick(cls_fields: set[str], raw: dict[str, Any]) -> dict[str, Any]:
    """Return only the keys that ``cls`` actually declares.

    YAML files often carry leftover / commented-out / forward-compat keys;
    silently dropping them is friendlier than failing the whole load.
    Type errors on **known** fields are still caught by the dataclass
    constructor and surface as :class:`ConfigError`.
    """
    return {k: v for k, v in raw.items() if k in cls_fields}


@dataclass(frozen=True)
class AppConfig:
    """Top-level config aggregate."""

    language: Language = "en"
    server: ServerConfig = field(default_factory=ServerConfig)
    callback: CallbackConfig = field(default_factory=CallbackConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    docs: DocsConfig = field(default_factory=DocsConfig)

    def __post_init__(self) -> None:
        # Validate language at construction time; everything else is
        # typed enough by the dataclass field types.
        if self.language not in ALLOWED_LANGUAGES:
            raise ConfigError(
                f"language 必须是 {'/'.join(ALLOWED_LANGUAGES)} 之一，"
                f"实际 {self.language!r}"
            )

    # ---------- construction ----------

    @classmethod
    def load(cls, path: str | Path) -> "AppConfig":
        """Load from YAML file.

        - File missing → return defaults (no error).
        - File present but malformed → raise :class:`ConfigError`.
        """
        p = Path(path)
        if not p.is_file():
            return cls()

        try:
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"读取 {p} 失败: {exc}") from exc

        if data is None:
            return cls()
        if not isinstance(data, dict):
            raise ConfigError(
                f"{p}: 顶层必须是 dict，实际 {type(data).__name__}"
            )
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """Build from a plain dict (useful for tests)."""
        language_raw = data.get("language", "en")
        if not isinstance(language_raw, str):
            raise ConfigError(f"language 必须是 str: {language_raw!r}")

        try:
            server = ServerConfig(**_pick(_SERVER_FIELDS, data.get("server") or {}))
            callback = CallbackConfig(**_pick(_CALLBACK_FIELDS, data.get("callback") or {}))
            plugins = PluginsConfig(**_pick(_PLUGINS_FIELDS, data.get("plugins") or {}))
            docs = DocsConfig(**_pick(_DOCS_FIELDS, data.get("docs") or {}))
        except TypeError as exc:
            raise ConfigError(f"配置字段不合法: {exc}") from exc

        return cls(
            language=language_raw,
            server=server,
            callback=callback,
            plugins=plugins,
            docs=docs,
        )

    # ---------- helpers ----------

    @property
    def is_zh(self) -> bool:
        """True iff language is set to Chinese."""
        return self.language == "zh"

    @property
    def is_en(self) -> bool:
        """True iff language is set to English."""
        return self.language == "en"
