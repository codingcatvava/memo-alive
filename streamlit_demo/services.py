from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

import numpy as np

from streamlit_demo import db
from streamlit_demo.ai import AIGateway, QWEN_EMBEDDING_MODEL
from streamlit_demo.models import DecisionType, MemoRelation, StructuredMemo


MAX_AUDIO_BYTES = 10 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
}


class ProcessingError(RuntimeError):
    def __init__(self, message: str, *, capture_id: Optional[str] = None) -> None:
        super().__init__(message)
        self.capture_id = capture_id


@dataclass(frozen=True)
class StoragePaths:
    base_dir: Path
    db_path: Path
    audio_dir: Path


@dataclass
class ProcessOutcome:
    memo: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


def prepare_storage(base_dir: Path) -> StoragePaths:
    base_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = base_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    paths = StoragePaths(base_dir=base_dir, db_path=base_dir / "app.db", audio_dir=audio_dir)
    db.initialize(paths.db_path)
    try:
        db.ensure_embedding_model(paths.db_path, QWEN_EMBEDDING_MODEL)
    except RuntimeError as exc:
        raise ProcessingError(str(exc)) from exc
    return paths


def _audio_extension(mime_type: str, original_name: Optional[str]) -> str:
    normalised_mime = (mime_type or "").split(";")[0].strip().lower()
    if normalised_mime in SUPPORTED_AUDIO_TYPES:
        return SUPPORTED_AUDIO_TYPES[normalised_mime]
    suffix = Path(original_name or "").suffix.lower()
    if suffix in set(SUPPORTED_AUDIO_TYPES.values()):
        return suffix
    raise ProcessingError("暂不支持这种音频格式，请使用 WAV、MP3、WebM 或 OGG。")


def _validate_audio(audio_bytes: bytes) -> None:
    if not audio_bytes:
        raise ProcessingError("音频为空，请重新录制或上传。")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise ProcessingError("音频超过 10 MB，请缩短录音后重试。")


def _write_audio_once(
    audio_dir: Path,
    *,
    capture_id: str,
    audio_bytes: bytes,
    extension: str,
) -> Tuple[Path, str]:
    audio_dir.mkdir(parents=True, exist_ok=True)
    final_path = audio_dir / f"{capture_id}{extension}"
    digest = hashlib.sha256(audio_bytes).hexdigest()
    if final_path.exists():
        existing_digest = hashlib.sha256(final_path.read_bytes()).hexdigest()
        if existing_digest != digest:
            raise ProcessingError("同一录音标识对应的原始音频不一致，已拒绝覆盖。")
        return final_path, digest

    temp_path = audio_dir / f".{capture_id}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("xb") as file_handle:
            file_handle.write(audio_bytes)
            file_handle.flush()
            os.fsync(file_handle.fileno())
        if final_path.exists():
            existing_digest = hashlib.sha256(final_path.read_bytes()).hexdigest()
            if existing_digest == digest:
                return final_path, digest
            raise ProcessingError("原始音频目标已存在且内容不同，已拒绝覆盖。")
        os.replace(str(temp_path), str(final_path))
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return final_path, digest


def verify_audio_integrity(memo: Dict[str, Any]) -> bool:
    path = Path(memo["audio_path"])
    if not path.is_file():
        return False
    current = hashlib.sha256(path.read_bytes()).hexdigest()
    capture = memo.get("capture")
    expected = capture.get("audio_sha256") if isinstance(capture, dict) else None
    if expected is None:
        expected = memo.get("audio_sha256")
    return bool(expected and current == expected)


def _validate_grounding(transcript: str, structured: StructuredMemo) -> None:
    rendered = "\n".join(
        [structured.title, structured.summary, structured.cleaned_markdown]
        + [unit.content for unit in structured.memory_units]
    )
    source_numbers = set(re.findall(r"\d+(?:\.\d+)?", transcript))
    rendered_numbers = set(re.findall(r"\d+(?:\.\d+)?", rendered))
    if not source_numbers.issubset(rendered_numbers):
        raise ProcessingError("AI 整理遗漏了原文中的数字，已停止写入。")
    source_has_negation = any(word in transcript for word in ("不", "没", "无", "别", "取消", "停止"))
    output_has_negation = any(word in rendered for word in ("不", "没", "无", "别", "取消", "停止"))
    if source_has_negation and not output_has_negation:
        raise ProcessingError("AI 整理丢失了否定含义，已停止写入。")


def cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return -1.0
    left_vector = np.asarray(left, dtype=float)
    right_vector = np.asarray(right, dtype=float)
    denominator = float(np.linalg.norm(left_vector) * np.linalg.norm(right_vector))
    if denominator == 0:
        return -1.0
    return float(np.dot(left_vector, right_vector) / denominator)


def _best_candidate(
    candidates: List[Dict[str, Any]],
    embedding: List[float],
) -> Tuple[Optional[Dict[str, Any]], float]:
    scored = [
        (candidate, cosine_similarity(embedding, candidate["embedding"]))
        for candidate in candidates
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[0] if scored else (None, -1.0)


def process_audio(
    paths: StoragePaths,
    gateway: AIGateway,
    *,
    audio_bytes: bytes,
    mime_type: str,
    original_name: Optional[str],
    location: Optional[str],
    operation_id: Optional[str] = None,
    similarity_threshold: float = 0.45,
) -> ProcessOutcome:
    """Save evidence first, then build a validated memo exactly once."""

    _validate_audio(audio_bytes)
    capture_id = operation_id or str(uuid4())
    existing_memo = db.get_memo_by_capture(paths.db_path, capture_id)
    if existing_memo:
        return ProcessOutcome(memo=existing_memo)

    extension = _audio_extension(mime_type, original_name)
    audio_path, audio_sha256 = _write_audio_once(
        paths.audio_dir,
        capture_id=capture_id,
        audio_bytes=audio_bytes,
        extension=extension,
    )
    capture = db.get_capture(paths.db_path, capture_id)
    if capture is None:
        db.create_capture(
            paths.db_path,
            capture_id=capture_id,
            audio_path=audio_path,
            audio_mime_type=mime_type or "application/octet-stream",
            audio_sha256=audio_sha256,
            audio_size_bytes=len(audio_bytes),
            location=(location or "").strip() or None,
        )
        capture = db.get_capture(paths.db_path, capture_id)
    elif capture["audio_sha256"] != audio_sha256:
        raise ProcessingError("同一操作提交了不同音频，已拒绝覆盖。", capture_id=capture_id)

    try:
        if capture and capture.get("raw_transcript"):
            transcript = capture["raw_transcript"]
        else:
            transcript = gateway.transcribe(audio_path).strip()
            db.set_capture_transcript(paths.db_path, capture_id, transcript)

        topics = [topic["name"] for topic in db.list_topics(paths.db_path)]
        structured = gateway.structure_memo(transcript, topics, (location or "").strip() or None)
        _validate_grounding(transcript, structured)

        text_for_embedding = f"{structured.summary}\n{structured.cleaned_markdown}"
        embedding = gateway.embed([text_for_embedding])[0]

        candidate, score = _best_candidate(
            db.list_candidate_memos(paths.db_path), embedding
        )
        relation: Optional[MemoRelation] = None
        old_memo_id: Optional[str] = None
        if candidate and score >= similarity_threshold:
            suggested = gateway.compare_memos(structured, candidate)
            if suggested.same_topic and suggested.relation_type != "unrelated":
                relation = suggested
                old_memo_id = candidate["id"]
                structured = structured.model_copy(
                    update={"domain": candidate["domain"], "topic": candidate["topic"]}
                )

        memo = db.save_processed_memo(
            paths.db_path,
            capture_id=capture_id,
            structured=structured,
            embedding=embedding,
            relation=relation,
            old_memo_id=old_memo_id,
        )
        memo["capture"] = db.get_capture(paths.db_path, capture_id)
        return ProcessOutcome(memo=memo)
    except Exception as exc:
        db.mark_capture_failed(paths.db_path, capture_id, str(exc))
        if isinstance(exc, ProcessingError):
            if exc.capture_id is None:
                exc.capture_id = capture_id
            raise
        raise ProcessingError(str(exc), capture_id=capture_id) from exc


def approve_relation(
    paths: StoragePaths,
    relation_id: str,
    decision: str,
    *,
    merge_draft: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if decision == "later":
        return None
    valid_decisions = {"keep_both", "use_new", "use_old", "ai_merge"}
    if decision not in valid_decisions:
        raise ValueError("unknown approval decision")
    final_summary = (merge_draft or "").strip() or None
    if decision == "ai_merge" and not final_summary:
        raise ValueError("AI 融合前需要确认一份非空融合稿")
    return db.apply_relation_decision(
        paths.db_path,
        relation_id,
        decision,  # type: ignore[arg-type]
        final_summary=final_summary,
    )


def edit_memo(
    paths: StoragePaths,
    memo_id: str,
    *,
    title: str,
    cleaned_markdown: str,
) -> Dict[str, Any]:
    return db.add_user_version(
        paths.db_path,
        memo_id,
        title=title,
        cleaned_markdown=cleaned_markdown,
    )
