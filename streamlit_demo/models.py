from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


MemoryUnitType = Literal[
    "thought",
    "task",
    "list_item",
    "plan",
    "decision",
    "preference",
    "fact",
    "event",
    "observation",
    "question",
    "reference",
    "habit",
    "other",
]

RelationType = Literal[
    "unrelated",
    "duplicate",
    "complement",
    "refinement",
    "update",
    "conditional",
    "conflict",
    "uncertain",
]

DecisionType = Literal["keep_both", "use_new", "use_old", "ai_merge"]


class MemoryUnit(BaseModel):
    """A small, source-grounded unit extracted from a transcript."""

    model_config = ConfigDict(extra="forbid")

    type: MemoryUnitType
    content: str = Field(min_length=1)
    people: List[str]
    projects: List[str]
    event_time: Optional[str]

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


class StructuredMemo(BaseModel):
    """Validated AI output used to create a memo."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=240)
    cleaned_markdown: str = Field(min_length=1)
    domain: str = Field(min_length=1, max_length=40)
    topic: str = Field(min_length=1, max_length=60)
    keywords: List[str]
    memory_units: List[MemoryUnit] = Field(min_length=1)
    timeline_view: str = Field(min_length=1, max_length=160)

    @field_validator(
        "title",
        "summary",
        "cleaned_markdown",
        "domain",
        "topic",
        "timeline_view",
    )
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("text fields must not be blank")
        return value

    @field_validator("keywords")
    @classmethod
    def keywords_must_not_be_empty(cls, value: List[str]) -> List[str]:
        cleaned = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not cleaned:
            raise ValueError("keywords must contain at least one item")
        return cleaned


class MemoRelation(BaseModel):
    """An AI suggestion. It never applies a user decision by itself."""

    model_config = ConfigDict(extra="forbid")

    same_topic: bool
    relation_type: RelationType
    differences: List[str]
    rationale: str = Field(min_length=1, max_length=500)
    one_line_change: str = Field(min_length=1, max_length=180)
    merge_draft: Optional[str]

    @field_validator("rationale", "one_line_change")
    @classmethod
    def relation_text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("relation text must not be blank")
        return value


class MemoMergeDraft(BaseModel):
    """Validated AI-generated topic summary draft used only after approval."""

    model_config = ConfigDict(extra="forbid")

    merged_summary: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=500)

    @field_validator("merged_summary", "rationale")
    @classmethod
    def merge_text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("merge text must not be blank")
        return value
