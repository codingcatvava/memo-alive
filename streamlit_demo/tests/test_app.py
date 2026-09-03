from pathlib import Path

import pytest


streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402


def test_app_loads_all_four_pages_without_exceptions(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-only-key")
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=20)
    app.run()
    assert not app.exception
    assert len(app.get("audio_input")) == 1

    pages = (
        ("history", "备忘录"),
        ("approval", "待审批"),
        ("timeline", "主题时间线"),
        ("record", "记下此刻，保留变化。"),
    )
    for page_key, expected_heading in pages:
        app.button(key=f"memo_nav_{page_key}").click().run()
        assert not app.exception
        assert any(expected_heading in item.value for item in app.markdown)

    rendered_text = "\n".join(item.value for item in app.markdown)
    assert "演示转写来源" not in rendered_text
    assert "手动输入实际转写" not in rendered_text


def test_app_stops_clearly_without_dashscope_key(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "")
    app_path = Path(__file__).resolve().parents[1] / "app.py"
    app = AppTest.from_file(str(app_path), default_timeout=20)
    app.run()
    assert not app.exception
    assert any("DASHSCOPE_API_KEY" in error.value for error in app.error)
    assert len(app.get("audio_input")) == 0
