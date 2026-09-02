from __future__ import annotations

import hashlib
import io
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import List

import pytest

from streamlit_demo import db
from streamlit_demo.ai import AIError
from streamlit_demo.services import (
    ProcessingError,
    approve_relation,
    edit_memo,
    prepare_storage,
    process_audio,
    verify_audio_integrity,
)
from streamlit_demo.tests.fakes import FakeQwenGateway


def wav_bytes(sample: int = 0) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16_000)
        wav.writeframes(int(sample).to_bytes(2, "little", signed=True) * 160)
    return buffer.getvalue()


def table_count(db_path: Path, table: str) -> int:
    with db.connection(db_path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def process_sample(paths, transcript: str, operation_id: str, sample: int = 0):
    return process_audio(
        paths,
        FakeQwenGateway(transcript),
        audio_bytes=wav_bytes(sample),
        mime_type="audio/wav",
        original_name="voice.wav",
        location="家里",
        operation_id=operation_id,
    )


def test_db_init_is_idempotent_and_reopenable(tmp_path: Path) -> None:
    paths = prepare_storage(tmp_path / "one")
    prepare_storage(tmp_path / "one")
    assert table_count(paths.db_path, "captures") == 0
    with db.connection(paths.db_path) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_database_rejects_vectors_from_another_model(tmp_path: Path) -> None:
    base_dir = tmp_path / "legacy"
    db_path = base_dir / "app.db"
    db.initialize(db_path)
    db.ensure_embedding_model(db_path, "legacy-vector-model")
    with pytest.raises(ProcessingError, match="不同的向量模型"):
        prepare_storage(base_dir)


def test_two_memos_create_pending_relation_and_keep_raw_evidence(tmp_path: Path) -> None:
    paths = prepare_storage(tmp_path / "session")
    first = process_sample(
        paths,
        "我日常出门要带钥匙、门禁卡和充电宝。",
        "capture-a",
        1,
    ).memo
    before_audio = Path(first["audio_path"]).read_bytes()
    before_transcript = first["raw_transcript"]

    second = process_sample(
        paths,
        "以后日常出门不用带充电宝，旅行的时候才带。",
        "capture-b",
        2,
    ).memo

    assert table_count(paths.db_path, "memos") == 2
    relations = db.list_pending_relations(paths.db_path)
    assert len(relations) == 1
    assert relations[0]["relation_type"] == "conflict"
    assert second["pending_approval_count"] == 1
    assert db.get_memo(paths.db_path, first["id"])["status"] == "active"
    assert Path(first["audio_path"]).read_bytes() == before_audio
    assert db.get_memo(paths.db_path, first["id"])["raw_transcript"] == before_transcript
    assert verify_audio_integrity(db.get_memo(paths.db_path, first["id"]))


def test_approval_is_idempotent_and_timeline_is_traceable(tmp_path: Path) -> None:
    paths = prepare_storage(tmp_path / "session")
    first = process_sample(
        paths,
        "我日常出门要带钥匙、门禁卡和充电宝。",
        "capture-a",
        1,
    ).memo
    second = process_sample(
        paths,
        "以后日常出门不用带充电宝，旅行的时候才带。",
        "capture-b",
        2,
    ).memo
    relation = db.list_pending_relations(paths.db_path)[0]

    first_decision = approve_relation(paths, relation["id"], "use_new")
    second_decision = approve_relation(paths, relation["id"], "use_new")
    assert first_decision["decision"] == "use_new"
    assert second_decision["decision"] == "use_new"
    assert table_count(paths.db_path, "relations") == 1
    assert table_count(paths.db_path, "memos") == 2

    topic = db.list_topics(paths.db_path)[0]
    events = db.list_timeline(paths.db_path, topic["id"])
    statuses = {event["memo_id"]: event["status"] for event in events}
    assert statuses[first["id"]] == "historical"
    assert statuses[second["id"]] == "current"
    assert all(event["source_memo_ids"] for event in events)


def test_user_edit_creates_version_without_touching_raw_capture(tmp_path: Path) -> None:
    paths = prepare_storage(tmp_path / "session")
    memo = process_sample(
        paths,
        "我日常出门要带钥匙、门禁卡和充电宝。",
        "capture-a",
        3,
    ).memo
    raw_before = memo["raw_transcript"]
    audio_before = hashlib.sha256(Path(memo["audio_path"]).read_bytes()).hexdigest()
    updated = edit_memo(
        paths,
        memo["id"],
        title="我的出门清单",
        cleaned_markdown="- 钥匙\n- 门禁卡",
    )
    assert updated["title"] == "我的出门清单"
    assert len(updated["versions"]) == 2
    assert updated["raw_transcript"] == raw_before
    assert hashlib.sha256(Path(updated["audio_path"]).read_bytes()).hexdigest() == audio_before
    assert verify_audio_integrity(updated)


def test_duplicate_submit_reuses_capture_and_memo(tmp_path: Path) -> None:
    paths = prepare_storage(tmp_path / "session")
    first = process_sample(paths, "今天记得带钥匙。", "same-operation", 4).memo
    second = process_sample(paths, "今天记得带钥匙。", "same-operation", 4).memo
    assert first["id"] == second["id"]
    assert table_count(paths.db_path, "captures") == 1
    assert table_count(paths.db_path, "memos") == 1


def test_concurrent_duplicate_submit_is_idempotent(tmp_path: Path) -> None:
    paths = prepare_storage(tmp_path / "session")

    def submit_once():
        return process_sample(
            paths,
            "我日常出门要带钥匙。",
            "concurrent-operation",
            7,
        ).memo["id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        memo_ids = list(executor.map(lambda _: submit_once(), range(2)))
    assert memo_ids[0] == memo_ids[1]
    assert table_count(paths.db_path, "captures") == 1
    assert table_count(paths.db_path, "memos") == 1


class BrokenStructuringGateway(FakeQwenGateway):
    def structure_memo(self, transcript: str, existing_topics: List[str], location):
        raise AIError("invalid structured output")


class BrokenEmbeddingGateway(FakeQwenGateway):
    def embed(self, texts: List[str]):
        raise AIError("embedding unavailable")


class BrokenRelationGateway(FakeQwenGateway):
    def compare_memos(self, new_memo, old_memo):
        raise AIError("relation unavailable")


def test_invalid_ai_output_keeps_capture_but_never_persists_memo(tmp_path: Path) -> None:
    paths = prepare_storage(tmp_path / "session")
    with pytest.raises(ProcessingError):
        process_audio(
            paths,
            BrokenStructuringGateway("测试原始转写"),
            audio_bytes=wav_bytes(5),
            mime_type="audio/wav",
            original_name="voice.wav",
            location=None,
            operation_id="broken-capture",
        )
    capture = db.get_capture(paths.db_path, "broken-capture")
    assert capture is not None
    assert capture["raw_transcript"] == "测试原始转写"
    assert Path(capture["audio_path"]).is_file()
    assert table_count(paths.db_path, "memos") == 0


def test_embedding_failure_has_no_runtime_fallback_and_retry_reuses_capture(
    tmp_path: Path,
) -> None:
    paths = prepare_storage(tmp_path / "session")
    operation_id = "embedding-retry"
    transcript = "今天记得带钥匙。"
    with pytest.raises(ProcessingError):
        process_audio(
            paths,
            BrokenEmbeddingGateway(transcript),
            audio_bytes=wav_bytes(8),
            mime_type="audio/wav",
            original_name="voice.wav",
            location=None,
            operation_id=operation_id,
        )
    capture = db.get_capture(paths.db_path, operation_id)
    assert capture["raw_transcript"] == transcript
    assert table_count(paths.db_path, "memos") == 0

    completed = process_audio(
        paths,
        FakeQwenGateway(transcript),
        audio_bytes=wav_bytes(8),
        mime_type="audio/wav",
        original_name="voice.wav",
        location=None,
        operation_id=operation_id,
    )
    assert completed.memo["raw_transcript"] == transcript
    assert table_count(paths.db_path, "captures") == 1
    assert table_count(paths.db_path, "memos") == 1


def test_relation_failure_does_not_save_incomplete_second_memo(tmp_path: Path) -> None:
    paths = prepare_storage(tmp_path / "session")
    process_sample(
        paths,
        "我日常出门要带钥匙、门禁卡和充电宝。",
        "capture-a",
        1,
    )
    transcript = "以后日常出门不用带充电宝，旅行的时候才带。"
    with pytest.raises(ProcessingError):
        process_audio(
            paths,
            BrokenRelationGateway(transcript),
            audio_bytes=wav_bytes(2),
            mime_type="audio/wav",
            original_name="voice.wav",
            location=None,
            operation_id="capture-b",
        )
    failed_capture = db.get_capture(paths.db_path, "capture-b")
    assert failed_capture["raw_transcript"] == transcript
    assert table_count(paths.db_path, "memos") == 1
    assert table_count(paths.db_path, "relations") == 0


@pytest.mark.parametrize(
    "payload,mime,name,message",
    [
        (b"", "audio/wav", "empty.wav", "音频为空"),
        (b"not-audio", "application/pdf", "note.pdf", "暂不支持"),
        (b"x" * (10 * 1024 * 1024 + 1), "audio/wav", "large.wav", "超过 10 MB"),
    ],
    ids=["empty", "unsupported", "oversize"],
)
def test_upload_validation_happens_before_persistence(
    tmp_path: Path,
    payload: bytes,
    mime: str,
    name: str,
    message: str,
) -> None:
    paths = prepare_storage(tmp_path / "session")
    with pytest.raises(ProcessingError, match=message):
        process_audio(
            paths,
            FakeQwenGateway("测试"),
            audio_bytes=payload,
            mime_type=mime,
            original_name=name,
            location=None,
        )
    assert table_count(paths.db_path, "captures") == 0


def test_two_sessions_are_isolated(tmp_path: Path) -> None:
    first_paths = prepare_storage(tmp_path / "session-a")
    second_paths = prepare_storage(tmp_path / "session-b")
    process_sample(first_paths, "我日常出门要带钥匙。", "capture", 6)
    assert table_count(first_paths.db_path, "memos") == 1
    assert table_count(second_paths.db_path, "memos") == 0
