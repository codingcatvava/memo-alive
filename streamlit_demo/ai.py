from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Type, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from streamlit_demo.models import MemoRelation, StructuredMemo


T = TypeVar("T", bound=BaseModel)

DEFAULT_VECTOR_DIMENSION = 1024
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MAX_INLINE_ASR_PAYLOAD_BYTES = 10 * 1024 * 1024

QWEN_ASR_MODEL = "qwen3-asr-flash"
QWEN_TEXT_MODEL = "qwen3.7-plus-2026-05-26"
QWEN_EMBEDDING_MODEL = "qwen3.7-text-embedding"

ASR_MIME_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
}


class AIError(RuntimeError):
    def __init__(self, message: str, code: str = "QWEN_PROVIDER_ERROR") -> None:
        super().__init__(message)
        self.code = code


class AIGateway(Protocol):
    mode: str

    def transcribe(self, audio_path: Path) -> str:
        ...

    def structure_memo(
        self,
        transcript: str,
        existing_topics: List[str],
        location: Optional[str],
    ) -> StructuredMemo:
        ...

    def embed(self, texts: List[str]) -> List[List[float]]:
        ...

    def compare_memos(
        self,
        new_memo: StructuredMemo,
        old_memo: Dict[str, Any],
    ) -> MemoRelation:
        ...


def _make_strict_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Apply the object constraints required by strict JSON Schema output."""

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                node["additionalProperties"] = False
                node["required"] = list(properties.keys())
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for item in node:
                visit(item)

    cloned = json.loads(json.dumps(schema))
    visit(cloned)
    return cloned


class QwenGateway:
    """Alibaba Cloud Model Studio adapter with no provider or model fallback."""

    mode = "real"
    provider = "aliyun_bailian"
    asr_model = QWEN_ASR_MODEL
    text_model = QWEN_TEXT_MODEL
    embedding_model = QWEN_EMBEDDING_MODEL

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        client: Optional[Any] = None,
    ) -> None:
        if not api_key.strip():
            raise AIError("未配置 DASHSCOPE_API_KEY，千问真实模式无法启动。", "QWEN_KEY_MISSING")
        self.base_url = (
            base_url
            or os.getenv("DASHSCOPE_COMPATIBLE_BASE_URL")
            or DEFAULT_DASHSCOPE_BASE_URL
        ).rstrip("/")
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(120.0, connect=20.0),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )

    def _raise_response_error(self, response: httpx.Response) -> None:
        if response.status_code in {401, 403}:
            raise AIError("百炼鉴权失败，请检查 API Key 与服务地域。", "QWEN_AUTH_FAILED")
        if response.status_code == 429:
            raise AIError("百炼请求被限流，请稍后使用同一条录音重试。", "QWEN_RATE_LIMITED")
        raise AIError(
            f"百炼请求失败（HTTP {response.status_code}），请检查模型权限、端点与请求额度。",
            "QWEN_HTTP_ERROR",
        )

    def _post_json(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = self.client.post(f"{self.base_url}/{endpoint.lstrip('/')}", json=payload)
        except httpx.TimeoutException as exc:
            raise AIError("百炼请求超时，请使用同一条录音重试。", "QWEN_TIMEOUT") from exc
        except httpx.RequestError as exc:
            raise AIError("无法连接阿里云百炼，请检查网络与服务地域。", "QWEN_NETWORK_ERROR") from exc
        if response.is_error:
            self._raise_response_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise AIError("百炼返回了无法解析的响应。", "QWEN_RESPONSE_INVALID") from exc
        if not isinstance(data, dict):
            raise AIError("百炼返回了意外的响应结构。", "QWEN_RESPONSE_INVALID")
        return data

    @staticmethod
    def _message_content(data: Dict[str, Any]) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("百炼返回内容为空。", "QWEN_RESPONSE_EMPTY") from exc
        if not isinstance(content, str) or not content.strip():
            raise AIError("百炼返回内容为空。", "QWEN_RESPONSE_EMPTY")
        return content.strip()

    def transcribe(self, audio_path: Path) -> str:
        mime_type = ASR_MIME_TYPES.get(audio_path.suffix.lower())
        if mime_type is None:
            raise AIError(
                "千问语音识别暂不支持该音频格式，请使用 WAV、MP3、WebM 或 OGG。",
                "QWEN_ASR_FORMAT_UNSUPPORTED",
            )
        try:
            audio_bytes = audio_path.read_bytes()
        except OSError as exc:
            raise AIError("无法读取已保存的原始音频。", "QWEN_ASR_AUDIO_READ_FAILED") from exc
        data_uri = f"data:{mime_type};base64,{base64.b64encode(audio_bytes).decode('ascii')}"
        if len(data_uri.encode("ascii")) > MAX_INLINE_ASR_PAYLOAD_BYTES:
            raise AIError(
                "音频经过 Base64 内联编码后超过百炼 10 MB 限制，请缩短录音后重试。",
                "QWEN_ASR_AUDIO_TOO_LARGE",
            )
        payload = {
            "model": self.asr_model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_uri},
                        }
                    ],
                }
            ],
            "stream": False,
            "asr_options": {"enable_itn": False},
        }
        return self._message_content(self._post_json("chat/completions", payload))

    def _structured(
        self,
        schema_model: Type[T],
        *,
        schema_name: str,
        instructions: str,
        input_text: str,
    ) -> T:
        schema = _make_strict_schema(schema_model.model_json_schema())
        original_user_message = input_text
        last_error: Optional[ValidationError] = None
        for attempt in range(2):
            user_message = original_user_message
            if attempt and last_error is not None:
                user_message += (
                    "\n\n上一份输出未通过 Pydantic 校验。只修复字段和格式，"
                    "不得改变原始事实。校验错误：\n"
                    + str(last_error)
                )
            payload = {
                "model": self.text_model,
                "messages": [
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0,
                "enable_thinking": False,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
            raw = self._message_content(self._post_json("chat/completions", payload))
            try:
                return schema_model.model_validate_json(raw)
            except ValidationError as exc:
                last_error = exc
        assert last_error is not None
        raise AIError(
            "千问结构化输出连续两次未通过 Pydantic 校验，已停止写入。",
            "QWEN_SCHEMA_INVALID",
        ) from last_error

    def structure_memo(
        self,
        transcript: str,
        existing_topics: List[str],
        location: Optional[str],
    ) -> StructuredMemo:
        instructions = (
            "你负责把中文语音备忘录整理成结构化记录。必须保留否定、条件、数字、人物和时间；"
            "不得添加原文没有的事实。原始转写是只读证据。主题优先复用给定的已有主题。"
        )
        input_text = (
            f"原始转写：\n{transcript}\n\n"
            f"已有主题：{json.dumps(existing_topics, ensure_ascii=False)}\n"
            f"手填地点：{location or '未填写'}"
        )
        return self._structured(
            StructuredMemo,
            schema_name="structured_memo",
            instructions=instructions,
            input_text=input_text,
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        data = self._post_json(
            "embeddings",
            {
                "model": self.embedding_model,
                "input": texts,
                "dimensions": DEFAULT_VECTOR_DIMENSION,
                "encoding_format": "float",
            },
        )
        try:
            items = data["data"]
            if not isinstance(items, list):
                raise TypeError
            ordered = sorted(items, key=lambda item: item.get("index", 0))
            vectors = [[float(value) for value in item["embedding"]] for item in ordered]
        except (KeyError, TypeError, ValueError) as exc:
            raise AIError("百炼向量响应无效。", "QWEN_EMBEDDING_INVALID") from exc
        if len(vectors) != len(texts) or any(
            len(vector) != DEFAULT_VECTOR_DIMENSION for vector in vectors
        ):
            raise AIError(
                "千问向量数量或维度不符合 1024 维约束，已停止写入。",
                "QWEN_EMBEDDING_DIMENSION_MISMATCH",
            )
        return vectors

    def compare_memos(
        self,
        new_memo: StructuredMemo,
        old_memo: Dict[str, Any],
    ) -> MemoRelation:
        instructions = (
            "比较同一用户的两条备忘录，只能建议关系和融合草稿，不能替用户做审批。"
            "重点区分重复、补充、细化、更新、条件变化和冲突；不得删除或改写来源事实。"
        )
        input_text = json.dumps(
            {
                "new": new_memo.model_dump(mode="json"),
                "old": {
                    "title": old_memo["title"],
                    "summary": old_memo["summary"],
                    "cleaned_markdown": old_memo["cleaned_markdown"],
                    "domain": old_memo["domain"],
                    "topic": old_memo["topic"],
                },
            },
            ensure_ascii=False,
        )
        return self._structured(
            MemoRelation,
            schema_name="memo_relation",
            instructions=instructions,
            input_text=input_text,
        )


def create_gateway(
    *, api_key: Optional[str], base_url: Optional[str] = None
) -> AIGateway:
    return QwenGateway(api_key or "", base_url=base_url)
