"""DocsGenerator 单元测试。"""

from __future__ import annotations

from pathlib import Path

from app.docs_gen.generator import DocsGenerator
from app.plugins.config import EndpointSpec, PluginConfig


def _plugin(name, eps, fields=None):
    return PluginConfig(
        name=name,
        token="t",
        description=f"{name} desc",
        fields=fields or {},
        endpoints=eps,
        file_path=Path(__file__),
    )


def test_generate_creates_index_all_and_per_endpoint(tmp_path: Path):
    plugin = _plugin("fan_weather", [
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
        EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
    ])
    DocsGenerator([plugin], version="0.1.0", output_dir=tmp_path).generate()
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "all.html").exists()
    assert (tmp_path / "weather.html").exists()
    assert (tmp_path / "weather_alert.html").exists()


def test_index_lists_each_endpoint_on_its_own_row(tmp_path: Path):
    plugin = _plugin("fan_weather", [
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
        EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
    ])
    DocsGenerator([plugin], version="0.1.0", output_dir=tmp_path).generate()
    html = (tmp_path / "index.html").read_text(encoding="utf-8")
    # 至少两个 <tr> 行
    assert html.count("<tr>") >= 2
    assert "/api/weather" in html
    assert "/api/weather_alert" in html
    # 排除标签
    assert "exclude_from_all" in html


def test_all_html_splits_endpoints_by_exclude_flag(tmp_path: Path):
    plugin = _plugin("fan_weather", [
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
        EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
    ])
    DocsGenerator([plugin], version="0.1.0", output_dir=tmp_path).generate()
    html = (tmp_path / "all.html").read_text(encoding="utf-8")
    # "包含在 /all" 部分
    assert "包含在 /all" in html
    assert "被排除" in html
    # /api/weather 应在包含区，/api/weather_alert 不应
    weather_pos = html.find("/api/weather")
    alert_pos = html.find("/api/weather_alert")
    assert weather_pos != -1
    assert alert_pos != -1
    # 包含区标题在 weather_pos 之前；weather_pos 在 excluded 标题之前
    included_pos = html.find("包含在 /all")
    excluded_pos = html.find("被排除")
    assert included_pos < weather_pos < excluded_pos < alert_pos


def test_endpoint_html_renders_fields_table(tmp_path: Path):
    plugin = _plugin(
        "fan_weather",
        [EndpointSpec(path="/api/weather", protocols=["http", "ws"])],
        fields={
            "temp": {"type": "number", "description": "温度", "nullable": False},
            "city": {"type": "string", "description": "城市", "nullable": True},
        },
    )
    DocsGenerator([plugin], version="0.1.0", output_dir=tmp_path).generate()
    html = (tmp_path / "weather.html").read_text(encoding="utf-8")
    assert "fan_weather" in html
    assert "temp" in html
    assert "city" in html
    assert "温度" in html
    assert "城市" in html
    # 字段名在 code 标签里
    assert "<code>temp</code>" in html


def test_endpoint_html_shows_protocols_and_exclude_flag(tmp_path: Path):
    plugin = _plugin("fan_weather", [
        EndpointSpec(path="/api/weather", protocols=["http", "ws"]),
        EndpointSpec(path="/api/weather_alert", protocols=["ws"], exclude_from_all=True),
    ])
    DocsGenerator([plugin], version="0.1.0", output_dir=tmp_path).generate()
    alert_html = (tmp_path / "weather_alert.html").read_text(encoding="utf-8")
    assert "ws" in alert_html
    assert "exclude_from_all" in alert_html


def test_generate_creates_output_dir_if_missing(tmp_path: Path):
    out = tmp_path / "sub" / "docs"
    plugin = _plugin("p", [EndpointSpec(path="/api/x", protocols=["http"])])
    DocsGenerator([plugin], version="0.1.0", output_dir=out).generate()
    assert out.is_dir()
    assert (out / "index.html").exists()


def test_derived_source_strips_api_prefix(tmp_path: Path):
    plugin = _plugin("p", [
        EndpointSpec(path="/api/foo/bar", protocols=["http"]),
    ])
    DocsGenerator([plugin], version="0.1.0", output_dir=tmp_path).generate()
    assert (tmp_path / "foo" / "bar.html").exists()  # 嵌套路径
    # 或保留为单文件
    # 实际上 derived_source 是 "foo/bar" 包含 /，写文件会创建子目录
    # 让我们验证文件存在
    assert (tmp_path / "foo").is_dir()
