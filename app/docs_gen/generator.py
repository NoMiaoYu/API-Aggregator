"""静态文档生成器：Jinja2 渲染。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.plugins.config import PluginConfig


@dataclass
class _EndpointView:
    """模板使用的扁平视图。"""

    plugin_name: str
    path: str
    protocols: list[str]
    exclude_from_all: bool
    derived_source: str  # endpoint.path.lstrip('/').removeprefix('api/')


def _derive_source(path: str) -> str:
    s = path.lstrip("/")
    if s.startswith("api/"):
        s = s[len("api/"):]
    return s


def _flatten_endpoints(plugins: Iterable[PluginConfig]) -> list[_EndpointView]:
    views: list[_EndpointView] = []
    for plugin in plugins:
        for ep in plugin.endpoints:
            views.append(
                _EndpointView(
                    plugin_name=plugin.name,
                    path=ep.path,
                    protocols=list(ep.protocols),
                    exclude_from_all=ep.exclude_from_all,
                    derived_source=_derive_source(ep.path),
                )
            )
    views.sort(key=lambda v: v.path)
    return views


class DocsGenerator:
    """在 output_dir 一次性生成 index / all / 每个 endpoint 的 HTML。"""

    def __init__(
        self,
        plugins: list[PluginConfig],
        version: str,
        output_dir: str | Path,
        template_dir: str | Path | None = None,
    ) -> None:
        self.plugins = plugins
        self.version = version
        self.output_dir = Path(output_dir)
        if template_dir is None:
            # 默认模板目录
            template_dir = Path(__file__).parent / "templates"
        self.template_dir = Path(template_dir)
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    # ---------- 公共 API ----------

    def generate(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        endpoints = _flatten_endpoints(self.plugins)
        # 按 endpoint 一对一查 plugin（用于 endpoint.html 渲染 fields）
        ep_to_plugin: dict[str, PluginConfig] = {}
        for plugin in self.plugins:
            for ep in plugin.endpoints:
                ep_to_plugin[ep.path] = plugin

        # index.html
        self._write("index.html", self._render_index(endpoints))

        # all.html
        self._write("all.html", self._render_all(endpoints))

        # 每个 endpoint 一页
        for ev in endpoints:
            plugin = ep_to_plugin[ev.path]
            page = self._render_endpoint(ev, plugin)
            self._write(f"{ev.derived_source}.html", page)

    # ---------- 渲染辅助 ----------

    def _write(self, name: str, html: str) -> None:
        target = self.output_dir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(html, encoding="utf-8")

    def _render_index(self, endpoints: list[_EndpointView]) -> str:
        tpl = self.env.get_template("index.html")
        return tpl.render(endpoints=endpoints, version=self.version)

    def _render_all(self, endpoints: list[_EndpointView]) -> str:
        all_eps = [e for e in endpoints if not e.exclude_from_all]
        excluded = [e for e in endpoints if e.exclude_from_all]
        tpl = self.env.get_template("all.html")
        return tpl.render(
            all_endpoints=all_eps,
            excluded_endpoints=excluded,
            version=self.version,
        )

    def _render_endpoint(self, ev: _EndpointView, plugin: PluginConfig) -> str:
        tpl = self.env.get_template("endpoint.html")
        return tpl.render(
            endpoint=ev,
            plugin_name=plugin.name,
            plugin_description=plugin.description,
            fields=plugin.fields,
            version=self.version,
        )
