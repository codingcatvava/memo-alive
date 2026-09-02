from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from uuid import uuid4

from streamlit_demo.models import DecisionType, MemoRelation, StructuredMemo


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS app_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS captures (
  id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  location TEXT,
  audio_path TEXT NOT NULL,
  audio_mime_type TEXT NOT NULL,
  audio_sha256 TEXT NOT NULL,
  audio_size_bytes INTEGER NOT NULL CHECK (audio_size_bytes > 0),
  raw_transcript TEXT,
  status TEXT NOT NULL CHECK (status IN ('audio_saved', 'transcribed', 'completed', 'failed')),
  error_message TEXT
);

CREATE TABLE IF NOT EXISTS topics (
  id TEXT PRIMARY KEY,
  domain TEXT NOT NULL,
  name TEXT NOT NULL,
  current_summary TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  UNIQUE(domain, name)
);

CREATE TABLE IF NOT EXISTS memos (
  id TEXT PRIMARY KEY,
  capture_id TEXT NOT NULL UNIQUE REFERENCES captures(id),
  created_at TEXT NOT NULL,
  domain TEXT NOT NULL,
  topic TEXT NOT NULL,
  embedding_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active'
    CHECK (status IN ('active', 'superseded', 'rejected_from_current')),
  current_version_id TEXT
);

CREATE TABLE IF NOT EXISTS memo_versions (
  id TEXT PRIMARY KEY,
  memo_id TEXT NOT NULL REFERENCES memos(id),
  version_no INTEGER NOT NULL,
  title TEXT NOT NULL,
  summary TEXT NOT NULL,
  cleaned_markdown TEXT NOT NULL,
  memory_units_json TEXT NOT NULL,
  keywords_json TEXT NOT NULL,
  source_memo_ids_json TEXT NOT NULL,
  change_reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(memo_id, version_no)
);

CREATE TABLE IF NOT EXISTS relations (
  id TEXT PRIMARY KEY,
  new_memo_id TEXT NOT NULL REFERENCES memos(id),
  old_memo_id TEXT NOT NULL REFERENCES memos(id),
  relation_type TEXT NOT NULL,
  differences_json TEXT NOT NULL,
  rationale TEXT NOT NULL,
  merge_draft TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'decided')),
  decision TEXT,
  decided_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(new_memo_id, old_memo_id)
);

CREATE TABLE IF NOT EXISTS timeline_events (
  id TEXT PRIMARY KEY,
  topic_id TEXT NOT NULL REFERENCES topics(id),
  memo_id TEXT NOT NULL REFERENCES memos(id),
  relation_id TEXT REFERENCES relations(id),
  occurred_at TEXT NOT NULL,
  one_line_view TEXT NOT NULL,
  one_line_change TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('current', 'historical', 'pending_approval', 'rejected')),
  source_memo_ids_json TEXT NOT NULL,
  UNIQUE(topic_id, memo_id)
);

CREATE INDEX IF NOT EXISTS memos_created_idx ON memos(created_at DESC);
CREATE INDEX IF NOT EXISTS relations_status_idx ON relations(status, created_at DESC);
CREATE INDEX IF NOT EXISTS timeline_topic_idx ON timeline_events(topic_id, occurred_at DESC);

CREATE TRIGGER IF NOT EXISTS captures_raw_transcript_immutable
BEFORE UPDATE OF raw_transcript ON captures
FOR EACH ROW
WHEN OLD.raw_transcript IS NOT NULL AND NEW.raw_transcript IS NOT OLD.raw_transcript
BEGIN
  SELECT RAISE(ABORT, 'raw_transcript is immutable once written');
END;

CREATE TRIGGER IF NOT EXISTS captures_audio_path_immutable
BEFORE UPDATE OF audio_path ON captures
FOR EACH ROW
WHEN NEW.audio_path IS NOT OLD.audio_path
BEGIN
  SELECT RAISE(ABORT, 'audio_path is immutable');
END;
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Optional[str], fallback: Any) -> Any:
    if value is None:
        return fallback
    return json.loads(value)


@contextmanager
def connection(
    db_path: Path,
    *,
    commit: bool = False,
    immediate: bool = False,
) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        if immediate:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize(db_path: Path) -> None:
    with connection(db_path, commit=True) as conn:
        conn.executescript(SCHEMA)


def ensure_embedding_model(db_path: Path, model_id: str) -> None:
    """Prevent vectors from different semantic spaces sharing one database."""

    with connection(db_path, commit=True, immediate=True) as conn:
        existing = conn.execute(
            "SELECT value FROM app_metadata WHERE key = 'embedding_model_id'"
        ).fetchone()
        if existing is not None:
            if existing["value"] != model_id:
                raise RuntimeError(
                    "该数据库由不同的向量模型创建，不能与当前千问向量混用。"
                )
            return
        memo_count = int(conn.execute("SELECT COUNT(*) FROM memos").fetchone()[0])
        if memo_count:
            raise RuntimeError(
                "该数据库已有未标注模型来源的向量，请换一个空目录后启动。"
            )
        conn.execute(
            "INSERT INTO app_metadata (key, value) VALUES ('embedding_model_id', ?)",
            (model_id,),
        )


def create_capture(
    db_path: Path,
    *,
    capture_id: str,
    audio_path: Path,
    audio_mime_type: str,
    audio_sha256: str,
    audio_size_bytes: int,
    location: Optional[str],
    created_at: Optional[str] = None,
) -> Dict[str, Any]:
    created_at = created_at or utc_now()
    with connection(db_path, commit=True, immediate=True) as conn:
        conn.execute(
            """
            INSERT INTO captures (
              id, created_at, location, audio_path, audio_mime_type,
              audio_sha256, audio_size_bytes, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'audio_saved')
            ON CONFLICT(id) DO NOTHING
            """,
            (
                capture_id,
                created_at,
                location or None,
                str(audio_path),
                audio_mime_type,
                audio_sha256,
                audio_size_bytes,
            ),
        )
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
        if row is None:
            raise RuntimeError("capture was not persisted")
        capture = dict(row)
        expected = (str(audio_path), audio_sha256, audio_size_bytes)
        actual = (
            capture["audio_path"],
            capture["audio_sha256"],
            capture["audio_size_bytes"],
        )
        if actual != expected:
            raise ValueError("capture id already belongs to different audio evidence")
    return capture


def set_capture_transcript(db_path: Path, capture_id: str, transcript: str) -> None:
    transcript = transcript.strip()
    if not transcript:
        raise ValueError("transcript must not be blank")
    with connection(db_path, commit=True) as conn:
        cursor = conn.execute(
            """
            UPDATE captures
            SET raw_transcript = ?, status = 'transcribed', error_message = NULL
            WHERE id = ? AND raw_transcript IS NULL
            """,
            (transcript, capture_id),
        )
        if cursor.rowcount != 1:
            row = conn.execute(
                "SELECT raw_transcript FROM captures WHERE id = ?", (capture_id,)
            ).fetchone()
            if row is None:
                raise LookupError("capture not found")
            if row["raw_transcript"] != transcript:
                raise ValueError("raw transcript cannot be overwritten")


def mark_capture_failed(db_path: Path, capture_id: str, message: str) -> None:
    with connection(db_path, commit=True) as conn:
        conn.execute(
            "UPDATE captures SET status = 'failed', error_message = ? WHERE id = ?",
            (message[:500], capture_id),
        )


def get_capture(db_path: Path, capture_id: str) -> Optional[Dict[str, Any]]:
    with connection(db_path) as conn:
        row = conn.execute("SELECT * FROM captures WHERE id = ?", (capture_id,)).fetchone()
    return dict(row) if row else None


def _decode_memo(row: sqlite3.Row) -> Dict[str, Any]:
    result = dict(row)
    result["keywords"] = _loads(result.pop("keywords_json", "[]"), [])
    result["memory_units"] = _loads(result.pop("memory_units_json", "[]"), [])
    result["source_memo_ids"] = _loads(result.pop("source_memo_ids_json", "[]"), [])
    result["embedding"] = _loads(result.pop("embedding_json", "[]"), [])
    return result


MEMO_SELECT = """
SELECT
  m.id,
  m.capture_id,
  m.created_at,
  m.domain,
  m.topic,
  m.embedding_json,
  m.status,
  m.current_version_id,
  c.location,
  c.audio_path,
  c.audio_mime_type,
  c.audio_sha256,
  c.audio_size_bytes,
  c.raw_transcript,
  v.version_no,
  v.title,
  v.summary,
  v.cleaned_markdown,
  v.memory_units_json,
  v.keywords_json,
  v.source_memo_ids_json,
  v.change_reason,
  v.created_at AS version_created_at,
  (
    SELECT COUNT(*) FROM relations r
    WHERE r.status = 'pending' AND (r.new_memo_id = m.id OR r.old_memo_id = m.id)
  ) AS pending_approval_count
FROM memos m
JOIN captures c ON c.id = m.capture_id
JOIN memo_versions v ON v.id = m.current_version_id
"""


def get_memo(db_path: Path, memo_id: str) -> Optional[Dict[str, Any]]:
    with connection(db_path) as conn:
        row = conn.execute(MEMO_SELECT + " WHERE m.id = ?", (memo_id,)).fetchone()
        if row is None:
            return None
        versions = conn.execute(
            """
            SELECT id, version_no, title, summary, cleaned_markdown,
                   source_memo_ids_json, change_reason, created_at
            FROM memo_versions WHERE memo_id = ? ORDER BY version_no DESC
            """,
            (memo_id,),
        ).fetchall()
    result = _decode_memo(row)
    result["versions"] = [
        {
            **dict(version),
            "source_memo_ids": _loads(version["source_memo_ids_json"], []),
        }
        for version in versions
    ]
    for version in result["versions"]:
        version.pop("source_memo_ids_json", None)
    return result


def get_memo_by_capture(db_path: Path, capture_id: str) -> Optional[Dict[str, Any]]:
    with connection(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM memos WHERE capture_id = ?", (capture_id,)
        ).fetchone()
    return get_memo(db_path, row["id"]) if row else None


def list_memos(db_path: Path) -> List[Dict[str, Any]]:
    with connection(db_path) as conn:
        rows = conn.execute(MEMO_SELECT + " ORDER BY m.created_at DESC").fetchall()
    return [_decode_memo(row) for row in rows]


def list_candidate_memos(db_path: Path) -> List[Dict[str, Any]]:
    return [
        memo
        for memo in list_memos(db_path)
        if memo["status"] in {"active", "superseded"}
    ]


def save_processed_memo(
    db_path: Path,
    *,
    capture_id: str,
    structured: StructuredMemo,
    embedding: List[float],
    relation: Optional[MemoRelation] = None,
    old_memo_id: Optional[str] = None,
) -> Dict[str, Any]:
    memo_id = str(uuid4())
    version_id = str(uuid4())
    relation_id = str(uuid4()) if relation and old_memo_id else None
    event_id = str(uuid4())
    now = utc_now()

    with connection(db_path, commit=True, immediate=True) as conn:
        existing = conn.execute(
            "SELECT id FROM memos WHERE capture_id = ?", (capture_id,)
        ).fetchone()
        if existing:
            existing_id = existing["id"]
            memo = get_memo(db_path, existing_id)
            if memo is None:
                raise RuntimeError("existing memo could not be read")
            return memo
        capture = conn.execute(
            "SELECT * FROM captures WHERE id = ?", (capture_id,)
        ).fetchone()
        if capture is None:
            raise LookupError("capture not found")
        if not capture["raw_transcript"]:
            raise ValueError("raw transcript must exist before memo creation")

        topic_row = conn.execute(
            "SELECT id, current_summary FROM topics WHERE domain = ? AND name = ?",
            (structured.domain, structured.topic),
        ).fetchone()
        if topic_row:
            topic_id = topic_row["id"]
        else:
            topic_id = str(uuid4())
            conn.execute(
                """
                INSERT INTO topics (id, domain, name, current_summary, updated_at)
                VALUES (?, ?, ?, '', ?)
                """,
                (topic_id, structured.domain, structured.topic, now),
            )

        conn.execute(
            """
            INSERT INTO memos (
              id, capture_id, created_at, domain, topic, embedding_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                memo_id,
                capture_id,
                capture["created_at"],
                structured.domain,
                structured.topic,
                _json(embedding),
            ),
        )
        conn.execute(
            """
            INSERT INTO memo_versions (
              id, memo_id, version_no, title, summary, cleaned_markdown,
              memory_units_json, keywords_json, source_memo_ids_json,
              change_reason, created_at
            ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, 'ai_structured', ?)
            """,
            (
                version_id,
                memo_id,
                structured.title,
                structured.summary,
                structured.cleaned_markdown,
                _json([unit.model_dump(mode="json") for unit in structured.memory_units]),
                _json(structured.keywords),
                _json([memo_id]),
                now,
            ),
        )
        conn.execute(
            "UPDATE memos SET current_version_id = ? WHERE id = ?",
            (version_id, memo_id),
        )

        if relation_id and relation and old_memo_id:
            conn.execute(
                """
                INSERT INTO relations (
                  id, new_memo_id, old_memo_id, relation_type,
                  differences_json, rationale, merge_draft, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relation_id,
                    memo_id,
                    old_memo_id,
                    relation.relation_type,
                    _json(relation.differences),
                    relation.rationale,
                    relation.merge_draft,
                    now,
                ),
            )
            event_status = "pending_approval"
            one_line_change = relation.one_line_change
        else:
            conn.execute(
                """
                UPDATE timeline_events SET status = 'historical'
                WHERE topic_id = ? AND status = 'current'
                """,
                (topic_id,),
            )
            conn.execute(
                "UPDATE topics SET current_summary = ?, updated_at = ? WHERE id = ?",
                (structured.summary, now, topic_id),
            )
            event_status = "current"
            one_line_change = "首次记录该主题" if not topic_row else "新增一条当前记录"

        conn.execute(
            """
            INSERT INTO timeline_events (
              id, topic_id, memo_id, relation_id, occurred_at,
              one_line_view, one_line_change, status, source_memo_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                topic_id,
                memo_id,
                relation_id,
                capture["created_at"],
                structured.timeline_view,
                one_line_change,
                event_status,
                _json([memo_id] + ([old_memo_id] if old_memo_id else [])),
            ),
        )
        conn.execute(
            "UPDATE captures SET status = 'completed', error_message = NULL WHERE id = ?",
            (capture_id,),
        )

    memo = get_memo(db_path, memo_id)
    if memo is None:
        raise RuntimeError("memo was not persisted")
    return memo


def _decode_relation(row: sqlite3.Row) -> Dict[str, Any]:
    result = dict(row)
    result["differences"] = _loads(result.pop("differences_json", "[]"), [])
    return result


RELATION_SELECT = """
SELECT
  r.*,
  nv.title AS new_title,
  nv.summary AS new_summary,
  nv.cleaned_markdown AS new_cleaned_markdown,
  ov.title AS old_title,
  ov.summary AS old_summary,
  ov.cleaned_markdown AS old_cleaned_markdown,
  nm.created_at AS new_created_at,
  om.created_at AS old_created_at,
  nm.topic AS topic,
  nm.domain AS domain
FROM relations r
JOIN memos nm ON nm.id = r.new_memo_id
JOIN memo_versions nv ON nv.id = nm.current_version_id
JOIN memos om ON om.id = r.old_memo_id
JOIN memo_versions ov ON ov.id = om.current_version_id
"""


def list_pending_relations(db_path: Path) -> List[Dict[str, Any]]:
    with connection(db_path) as conn:
        rows = conn.execute(
            RELATION_SELECT + " WHERE r.status = 'pending' ORDER BY r.created_at DESC"
        ).fetchall()
    return [_decode_relation(row) for row in rows]


def get_relation(db_path: Path, relation_id: str) -> Optional[Dict[str, Any]]:
    with connection(db_path) as conn:
        row = conn.execute(
            RELATION_SELECT + " WHERE r.id = ?", (relation_id,)
        ).fetchone()
    return _decode_relation(row) if row else None


def apply_relation_decision(
    db_path: Path,
    relation_id: str,
    decision: DecisionType,
    *,
    final_summary: Optional[str] = None,
) -> Dict[str, Any]:
    now = utc_now()
    with connection(db_path, commit=True, immediate=True) as conn:
        relation = conn.execute(
            "SELECT * FROM relations WHERE id = ?", (relation_id,)
        ).fetchone()
        if relation is None:
            raise LookupError("relation not found")
        if relation["status"] != "pending":
            existing = get_relation(db_path, relation_id)
            if existing is None:
                raise RuntimeError("decided relation could not be read")
            return existing

        new_memo = conn.execute(
            """
            SELECT m.*, v.summary FROM memos m
            JOIN memo_versions v ON v.id = m.current_version_id
            WHERE m.id = ?
            """,
            (relation["new_memo_id"],),
        ).fetchone()
        old_memo = conn.execute(
            """
            SELECT m.*, v.summary FROM memos m
            JOIN memo_versions v ON v.id = m.current_version_id
            WHERE m.id = ?
            """,
            (relation["old_memo_id"],),
        ).fetchone()
        if new_memo is None or old_memo is None:
            raise RuntimeError("relation source memo is missing")

        topic = conn.execute(
            "SELECT * FROM topics WHERE domain = ? AND name = ?",
            (new_memo["domain"], new_memo["topic"]),
        ).fetchone()
        if topic is None:
            raise RuntimeError("relation topic is missing")

        if decision == "use_new":
            summary = new_memo["summary"]
            conn.execute(
                "UPDATE memos SET status = 'superseded' WHERE id = ?",
                (old_memo["id"],),
            )
            conn.execute(
                "UPDATE memos SET status = 'active' WHERE id = ?",
                (new_memo["id"],),
            )
        elif decision == "use_old":
            summary = old_memo["summary"]
            conn.execute(
                "UPDATE memos SET status = 'rejected_from_current' WHERE id = ?",
                (new_memo["id"],),
            )
            conn.execute(
                "UPDATE memos SET status = 'active' WHERE id = ?",
                (old_memo["id"],),
            )
        else:
            summary = (final_summary or relation["merge_draft"] or "").strip()
            if not summary:
                summary = f"{old_memo['summary']}；{new_memo['summary']}"
            conn.execute(
                "UPDATE memos SET status = 'active' WHERE id IN (?, ?)",
                (new_memo["id"], old_memo["id"]),
            )

        conn.execute(
            "UPDATE timeline_events SET status = 'historical' WHERE topic_id = ? AND status = 'current'",
            (topic["id"],),
        )
        if decision == "use_old":
            conn.execute(
                "UPDATE timeline_events SET status = 'rejected' WHERE relation_id = ?",
                (relation_id,),
            )
            conn.execute(
                "UPDATE timeline_events SET status = 'current' WHERE topic_id = ? AND memo_id = ?",
                (topic["id"], old_memo["id"]),
            )
        else:
            conn.execute(
                "UPDATE timeline_events SET status = 'current' WHERE relation_id = ?",
                (relation_id,),
            )

        conn.execute(
            "UPDATE topics SET current_summary = ?, updated_at = ? WHERE id = ?",
            (summary, now, topic["id"]),
        )
        conn.execute(
            """
            UPDATE relations
            SET status = 'decided', decision = ?, decided_at = ?,
                merge_draft = CASE WHEN ? = 'ai_merge' THEN ? ELSE merge_draft END
            WHERE id = ? AND status = 'pending'
            """,
            (decision, now, decision, summary, relation_id),
        )

    decided = get_relation(db_path, relation_id)
    if decided is None:
        raise RuntimeError("relation decision was not persisted")
    return decided


def add_user_version(
    db_path: Path,
    memo_id: str,
    *,
    title: str,
    cleaned_markdown: str,
) -> Dict[str, Any]:
    title = title.strip()
    cleaned_markdown = cleaned_markdown.strip()
    if not title or not cleaned_markdown:
        raise ValueError("title and cleaned memo must not be blank")
    now = utc_now()
    with connection(db_path, commit=True) as conn:
        memo = conn.execute(
            """
            SELECT m.*, v.summary, v.memory_units_json, v.keywords_json,
                   v.source_memo_ids_json
            FROM memos m JOIN memo_versions v ON v.id = m.current_version_id
            WHERE m.id = ?
            """,
            (memo_id,),
        ).fetchone()
        if memo is None:
            raise LookupError("memo not found")
        next_version = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 FROM memo_versions WHERE memo_id = ?",
            (memo_id,),
        ).fetchone()[0]
        version_id = str(uuid4())
        summary = " ".join(
            line.strip().lstrip("#- ") for line in cleaned_markdown.splitlines() if line.strip()
        )[:240]
        conn.execute(
            """
            INSERT INTO memo_versions (
              id, memo_id, version_no, title, summary, cleaned_markdown,
              memory_units_json, keywords_json, source_memo_ids_json,
              change_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'user_edit', ?)
            """,
            (
                version_id,
                memo_id,
                next_version,
                title,
                summary or title,
                cleaned_markdown,
                memo["memory_units_json"],
                memo["keywords_json"],
                memo["source_memo_ids_json"],
                now,
            ),
        )
        conn.execute(
            "UPDATE memos SET current_version_id = ? WHERE id = ?",
            (version_id, memo_id),
        )
        current_event = conn.execute(
            "SELECT topic_id FROM timeline_events WHERE memo_id = ? AND status = 'current'",
            (memo_id,),
        ).fetchone()
        if current_event:
            conn.execute(
                "UPDATE topics SET current_summary = ?, updated_at = ? WHERE id = ?",
                (summary or title, now, current_event["topic_id"]),
            )
    updated = get_memo(db_path, memo_id)
    if updated is None:
        raise RuntimeError("memo edit was not persisted")
    return updated


def list_topics(db_path: Path) -> List[Dict[str, Any]]:
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT t.*, COUNT(e.id) AS event_count
            FROM topics t LEFT JOIN timeline_events e ON e.topic_id = t.id
            GROUP BY t.id ORDER BY t.updated_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_timeline(db_path: Path, topic_id: str) -> List[Dict[str, Any]]:
    with connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT e.*, t.domain, t.name AS topic_name, v.title, v.summary
            FROM timeline_events e
            JOIN topics t ON t.id = e.topic_id
            JOIN memos m ON m.id = e.memo_id
            JOIN memo_versions v ON v.id = m.current_version_id
            WHERE e.topic_id = ? ORDER BY e.occurred_at DESC
            """,
            (topic_id,),
        ).fetchall()
    result: List[Dict[str, Any]] = []
    for row in rows:
        event = dict(row)
        event["source_memo_ids"] = _loads(event.pop("source_memo_ids_json"), [])
        result.append(event)
    return result
