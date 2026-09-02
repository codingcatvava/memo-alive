from __future__ import annotations

import pytest
from pydantic import ValidationError

from streamlit_demo.models import MemoMergeDraft, MemoRelation, MemoryUnit, StructuredMemo


def valid_memo() -> StructuredMemo:
    return StructuredMemo(
        title="出门清单",
        summary="日常出门带钥匙。",
        cleaned_markdown="- 钥匙",
        domain="生活管理",
        topic="出门清单",
        keywords=["钥匙"],
        memory_units=[
            MemoryUnit(
                type="list_item",
                content="钥匙",
                people=[],
                projects=[],
                event_time=None,
            )
        ],
        timeline_view="首次记录出门清单。",
    )


def test_structured_models_validate_and_forbid_extra_fields() -> None:
    assert valid_memo().topic == "出门清单"
    relation = MemoRelation(
        same_topic=True,
        relation_type="conflict",
        differences=["携带条件变化"],
        rationale="新旧规则不同。",
        one_line_change="充电宝规则变化",
        merge_draft="日常不带，旅行带。",
    )
    assert relation.relation_type == "conflict"
    assert MemoMergeDraft(merged_summary="融合结果", rationale="保留条件").merged_summary

    payload = valid_memo().model_dump()
    payload["unexpected"] = "should fail"
    with pytest.raises(ValidationError):
        StructuredMemo.model_validate(payload)


def test_structured_memo_rejects_blank_and_unknown_relation() -> None:
    payload = valid_memo().model_dump()
    payload["title"] = "  "
    with pytest.raises(ValidationError):
        StructuredMemo.model_validate(payload)
    with pytest.raises(ValidationError):
        MemoRelation(
            same_topic=True,
            relation_type="magic",
            differences=[],
            rationale="unknown",
            one_line_change="unknown",
            merge_draft=None,
        )
