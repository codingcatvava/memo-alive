from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import httpx
import pytest

from streamlit_demo.ai import (
    AIError,
    QWEN_ASR_MODEL,
    QWEN_EMBEDDING_MODEL,
    QWEN_TEXT_MODEL,
    QwenGateway,
)
from streamlit_demo.models import StructuredMemo


VALID_MEMO = {
    "title": "日常出门清单",
    "summary": "日常出门携带钥匙。",
    "cleaned_markdown": "- 钥匙",
    "domain": "生活管理",
    "topic": "出门清单",
    "keywords": ["出门", "钥匙"],
    "memory_units": [
        {
            "type": "list_item",
            "content": "钥匙",
            "people": [],
            "projects": [],
            "event_time": None,
        }
    ],
    "timeline_view": "记录日常出门物品。",
}


class FakeClient:
    def __init__(self, responses: List[httpx.Response]) -> None:
        self.responses = responses
        self.calls: List[Dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        self.calls.append({"url": url, **kwargs})
        return self.responses[len(self.calls) - 1]


def gateway_with(*responses: httpx.Response) -> tuple[QwenGateway, FakeClient]:
    client = FakeClient(list(responses))
    gateway = QwenGateway(
        "test-key",
        base_url="https://example.invalid/compatible-mode/v1",
        client=client,
    )
    return gateway, client


def completion(content: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={"choices": [{"message": {"content": content}}]},
    )


def test_real_gateway_requires_dashscope_key() -> None:
    with pytest.raises(AIError, match="DASHSCOPE_API_KEY") as error:
        QwenGateway("")
    assert error.value.code == "QWEN_KEY_MISSING"


def test_transcribe_inlines_audio_and_uses_qwen_asr(tmp_path: Path) -> None:
    audio = tmp_path / "private-recording.wav"
    audio.write_bytes(b"RIFF-local-audio")
    gateway, client = gateway_with(completion("今天下午三点开会。"))

    assert gateway.transcribe(audio) == "今天下午三点开会。"
    call = client.calls[0]
    assert call["url"].endswith("/chat/completions")
    payload = call["json"]
    assert payload["model"] == QWEN_ASR_MODEL
    assert payload["asr_options"] == {"enable_itn": False}
    data_uri = payload["messages"][0]["content"][0]["input_audio"]["data"]
    assert data_uri.startswith("data:audio/wav;base64,")
    assert str(audio) not in data_uri


def test_transcribe_rejects_oversize_encoded_audio_before_network(tmp_path: Path) -> None:
    audio = tmp_path / "large.wav"
    audio.write_bytes(b"x" * (8 * 1024 * 1024))
    gateway, client = gateway_with(completion("unused"))

    with pytest.raises(AIError, match="Base64") as error:
        gateway.transcribe(audio)
    assert error.value.code == "QWEN_ASR_AUDIO_TOO_LARGE"
    assert client.calls == []


def test_structure_uses_strict_schema_and_repairs_once() -> None:
    gateway, client = gateway_with(
        completion(json.dumps({"title": "缺字段"}, ensure_ascii=False)),
        completion(json.dumps(VALID_MEMO, ensure_ascii=False)),
    )

    memo = gateway.structure_memo("日常出门带钥匙。", ["出门清单"], "家里")
    assert memo == StructuredMemo.model_validate(VALID_MEMO)
    assert len(client.calls) == 2
    first_payload = client.calls[0]["json"]
    assert first_payload["model"] == QWEN_TEXT_MODEL
    assert first_payload["enable_thinking"] is False
    response_format = first_payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert "Pydantic" in client.calls[1]["json"]["messages"][1]["content"]


def test_structure_stops_after_one_failed_repair() -> None:
    invalid = completion(json.dumps({"title": "仍然缺字段"}, ensure_ascii=False))
    gateway, client = gateway_with(invalid, invalid)
    with pytest.raises(AIError, match="Pydantic") as error:
        gateway.structure_memo("日常出门带钥匙。", [], None)
    assert error.value.code == "QWEN_SCHEMA_INVALID"
    assert len(client.calls) == 2


def test_embedding_uses_qwen_and_rejects_wrong_dimension() -> None:
    good_vector = [0.0] * 1024
    good_gateway, good_client = gateway_with(
        httpx.Response(200, json={"data": [{"index": 0, "embedding": good_vector}]})
    )
    assert good_gateway.embed(["测试"])[0] == good_vector
    payload = good_client.calls[0]["json"]
    assert payload == {
        "model": QWEN_EMBEDDING_MODEL,
        "input": ["测试"],
        "dimensions": 1024,
        "encoding_format": "float",
    }

    bad_gateway, _ = gateway_with(
        httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.0]}]})
    )
    with pytest.raises(AIError, match="1024") as error:
        bad_gateway.embed(["测试"])
    assert error.value.code == "QWEN_EMBEDDING_DIMENSION_MISMATCH"


def test_relation_is_only_a_qwen_candidate_for_user_approval() -> None:
    relation_payload = {
        "same_topic": True,
        "relation_type": "conflict",
        "differences": ["携带规则发生变化"],
        "rationale": "同一事项的新旧要求不能同时成立。",
        "one_line_change": "充电宝从必带改为不带",
        "merge_draft": "旅行时携带充电宝。",
    }
    gateway, client = gateway_with(
        completion(json.dumps(relation_payload, ensure_ascii=False))
    )
    memo = StructuredMemo.model_validate(VALID_MEMO)
    relation = gateway.compare_memos(
        memo,
        {
            "title": "旧清单",
            "summary": "旧记录要求带充电宝。",
            "cleaned_markdown": "- 充电宝",
            "domain": "生活管理",
            "topic": "出门清单",
        },
    )
    assert relation.relation_type == "conflict"
    payload = client.calls[0]["json"]
    assert payload["model"] == QWEN_TEXT_MODEL
    assert payload["response_format"]["json_schema"]["name"] == "memo_relation"
    assert "审批" in payload["messages"][0]["content"]


def test_auth_error_is_safe_and_does_not_expose_key(tmp_path: Path) -> None:
    audio = tmp_path / "voice.wav"
    audio.write_bytes(b"RIFF")
    gateway, _ = gateway_with(httpx.Response(401, json={"message": "unauthorized"}))
    with pytest.raises(AIError) as error:
        gateway.transcribe(audio)
    assert error.value.code == "QWEN_AUTH_FAILED"
    assert "test-key" not in str(error.value)
