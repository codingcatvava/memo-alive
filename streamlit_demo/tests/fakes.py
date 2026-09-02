from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

from streamlit_demo.models import MemoRelation, MemoryUnit, StructuredMemo


def _normalise(text: str) -> str:
    return re.sub(r"[\s，。！？、；：,.!?;:\-—_（）()\[\]]+", "", text).lower()


def _keywords(text: str) -> List[str]:
    vocabulary = ["出门", "钥匙", "门禁卡", "充电宝", "旅行", "日常", "工作", "计划"]
    found = [word for word in vocabulary if word in text]
    return found[:8] or ["记录"]


def _tokens(text: str) -> Iterable[str]:
    normalised = _normalise(text)
    yield from normalised
    for index in range(max(0, len(normalised) - 1)):
        yield normalised[index : index + 2]
    for keyword in _keywords(text):
        yield f"keyword:{keyword}"


def _embedding(text: str, dimensions: int = 1024) -> List[float]:
    vector = np.zeros(dimensions, dtype=float)
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = float(np.linalg.norm(vector))
    if math.isclose(norm, 0.0):
        vector[0] = 1.0
        norm = 1.0
    return (vector / norm).tolist()


class FakeQwenGateway:
    """Deterministic test double. It is never imported by production code."""

    mode = "test"

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript

    def transcribe(self, audio_path: Path) -> str:
        assert audio_path.is_file()
        return self.transcript

    def structure_memo(
        self,
        transcript: str,
        existing_topics: List[str],
        location: Optional[str],
    ) -> StructuredMemo:
        del existing_topics, location
        if "充电宝" in transcript and any(
            marker in transcript for marker in ("不用带", "不带", "无需带")
        ):
            return StructuredMemo(
                title="出门携带规则调整",
                summary="日常出门不带充电宝，旅行时才携带。",
                cleaned_markdown="- 日常出门：不带充电宝\n- 旅行：携带充电宝",
                domain="生活管理",
                topic="出门清单",
                keywords=["出门", "充电宝", "旅行"],
                memory_units=[
                    MemoryUnit(
                        type="decision",
                        content="日常出门不带充电宝",
                        people=[],
                        projects=[],
                        event_time=None,
                    ),
                    MemoryUnit(
                        type="preference",
                        content="旅行时携带充电宝",
                        people=[],
                        projects=[],
                        event_time=None,
                    ),
                ],
                timeline_view="日常不再带充电宝，旅行场景仍保留。",
            )
        if any(word in transcript for word in ("出门", "钥匙", "门禁卡", "充电宝")):
            items = [item for item in ("钥匙", "门禁卡", "充电宝") if item in transcript]
            return StructuredMemo(
                title="日常出门清单",
                summary="日常出门需要携带" + "、".join(items) + "。",
                cleaned_markdown="\n".join(f"- {item}" for item in items),
                domain="生活管理",
                topic="出门清单",
                keywords=_keywords(transcript),
                memory_units=[
                    MemoryUnit(
                        type="list_item",
                        content=item,
                        people=[],
                        projects=[],
                        event_time=None,
                    )
                    for item in items
                ],
                timeline_view="记录日常出门随身物品。",
            )
        cleaned = transcript.strip()
        return StructuredMemo(
            title=cleaned[:18],
            summary=cleaned[:120],
            cleaned_markdown=cleaned,
            domain="日常记录",
            topic="随手记",
            keywords=["记录"],
            memory_units=[
                MemoryUnit(
                    type="task" if "要" in cleaned or "记得" in cleaned else "thought",
                    content=cleaned,
                    people=[],
                    projects=[],
                    event_time=None,
                )
            ],
            timeline_view=cleaned[:160],
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        return [_embedding(text) for text in texts]

    def compare_memos(
        self,
        new_memo: StructuredMemo,
        old_memo: Dict[str, Any],
    ) -> MemoRelation:
        new_text = f"{new_memo.summary}\n{new_memo.cleaned_markdown}"
        old_text = f"{old_memo['summary']}\n{old_memo['cleaned_markdown']}"
        if new_memo.topic != old_memo["topic"]:
            return MemoRelation(
                same_topic=False,
                relation_type="unrelated",
                differences=["主题不同"],
                rationale="两条记录属于不同主题。",
                one_line_change="新增另一主题记录",
                merge_draft=None,
            )
        if "充电宝" in new_text and "充电宝" in old_text and "不带" in new_text:
            return MemoRelation(
                same_topic=True,
                relation_type="conflict",
                differences=["旧记录日常携带充电宝", "新记录日常不带充电宝"],
                rationale="同一物品的携带规则发生变化。",
                one_line_change="充电宝改为仅旅行携带",
                merge_draft="日常携带钥匙和门禁卡；旅行时携带充电宝。",
            )
        return MemoRelation(
            same_topic=True,
            relation_type="complement",
            differences=["新记录增加了内容"],
            rationale="同一主题包含补充内容。",
            one_line_change="新增补充内容",
            merge_draft=f"{old_memo['summary']}；{new_memo.summary}",
        )
