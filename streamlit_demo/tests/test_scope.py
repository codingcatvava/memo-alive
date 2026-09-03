from pathlib import Path


def test_streamlit_copy_has_no_question_answer_feature() -> None:
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in root.glob("*.py")
        if path.name != "__init__.py"
    ).lower()
    forbidden_terms = (
        "ask_" + "memory",
        "memory" + "answer",
        "question_" + "logs",
        "gpt-4o-mini-" + "tts",
        "问" + "记忆",
    )
    for forbidden in forbidden_terms:
        assert forbidden not in source


def test_app_declares_only_the_four_requested_pages() -> None:
    app_source = (Path(__file__).resolve().parents[1] / "app.py").read_text(
        encoding="utf-8"
    )
    for expected in ("录音", "备忘录", "待审批", "时间线"):
        assert expected in app_source


def test_production_uses_only_real_qwen_provider() -> None:
    root = Path(__file__).resolve().parents[1]
    production_source = "\n".join(
        path.read_text(encoding="utf-8") for path in root.glob("*.py")
    )
    forbidden_terms = (
        "Open" + "AIGateway",
        "OPENAI" + "_",
        "gpt" + "-",
        "Mock" + "Gateway",
        "AI_" + "MODE",
        "transcript_" + "override",
    )
    for forbidden in forbidden_terms:
        assert forbidden not in production_source

    requirements = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "open" + "ai" not in requirements
    assert "httpx==" in requirements

    app_source = (root / "app.py").read_text(encoding="utf-8")
    assert "演示转写来源" not in app_source
    assert "手动输入实际转写" not in app_source
    assert "DASHSCOPE_API_KEY" in app_source
