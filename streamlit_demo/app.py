from __future__ import annotations

import hashlib
import html
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import streamlit as st

from streamlit_demo import db
from streamlit_demo.ai import (
    AIError,
    QWEN_ASR_MODEL,
    QWEN_EMBEDDING_MODEL,
    QWEN_TEXT_MODEL,
    create_gateway,
)
from streamlit_demo.services import (
    ProcessingError,
    StoragePaths,
    approve_relation,
    edit_memo,
    prepare_storage,
    process_audio,
    verify_audio_integrity,
)


APP_DIR = Path(__file__).resolve().parent
STATUS_LABELS = {
    "active": "当前记录",
    "superseded": "历史记录",
    "rejected_from_current": "未采用",
    "current": "当前",
    "historical": "历史",
    "pending_approval": "待审批",
    "rejected": "未采用",
}
DECISION_LABELS = {
    "keep_both": "两个都保留",
    "use_new": "采用新的",
    "use_old": "采用旧的",
    "ai_merge": "确认 AI 融合稿",
    "later": "稍后处理",
}


st.set_page_config(
    page_title="Memo Alive · 交互演示",
    page_icon="🎙️",
    layout="centered",
    initial_sidebar_state="collapsed",
)


def _load_css() -> None:
    css = (APP_DIR / "styles.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def _secret(name: str, default: Optional[str] = None) -> Optional[str]:
    environment_value = os.getenv(name)
    if environment_value is not None:
        return environment_value
    try:
        value = st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        value = None
    return str(value) if value is not None else default


def _storage_paths() -> StoragePaths:
    storage_mode = (_secret("DEMO_STORAGE_MODE", "session") or "session").lower()
    if storage_mode == "persistent":
        configured = _secret("DEMO_DATA_DIR")
        base_dir = Path(configured).expanduser() if configured else APP_DIR / "data"
        if not base_dir.is_absolute():
            base_dir = APP_DIR / base_dir
    else:
        if "demo_session_id" not in st.session_state:
            st.session_state.demo_session_id = uuid4().hex
        base_dir = (
            Path(tempfile.gettempdir())
            / "memo-alive-streamlit"
            / st.session_state.demo_session_id
        )
    return prepare_storage(base_dir.resolve())


@st.cache_resource
def _cached_gateway(api_key: str, base_url: Optional[str]) -> Any:
    return create_gateway(api_key=api_key, base_url=base_url)


def _gateway() -> Any:
    api_key = _secret("DASHSCOPE_API_KEY") or ""
    base_url = _secret("DASHSCOPE_COMPATIBLE_BASE_URL")
    return _cached_gateway(api_key, base_url)


def _status_badge(status: str) -> str:
    label = STATUS_LABELS.get(status, status)
    safe_status = html.escape(status)
    return f'<span class="memo-status {safe_status}">{html.escape(label)}</span>'


def _render_header() -> None:
    st.markdown(
        f"""
        <div class="memo-header">
          <div class="memo-brand"><span>Memo</span> Alive</div>
          <div class="memo-mode">千问真实模式</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_memo_result(memo: Dict[str, Any]) -> None:
    st.markdown('<div class="memo-card">', unsafe_allow_html=True)
    status_columns = st.columns([4, 1])
    with status_columns[0]:
        st.subheader(memo["title"])
        st.caption(memo["summary"])
    with status_columns[1]:
        badge_status = "pending" if memo["pending_approval_count"] else memo["status"]
        st.markdown(_status_badge(badge_status), unsafe_allow_html=True)
    st.markdown(memo["cleaned_markdown"])
    metadata = st.columns(3)
    metadata[0].metric("领域", memo["domain"])
    metadata[1].metric("主题", memo["topic"])
    metadata[2].metric("待审批", memo["pending_approval_count"])
    with st.expander("查看只读原始证据"):
        audio_path = Path(memo["audio_path"])
        if audio_path.is_file():
            st.audio(str(audio_path), format=memo["audio_mime_type"])
        st.text_area(
            "原始转写",
            memo["raw_transcript"],
            disabled=True,
            key=f"raw-{memo['id']}",
        )
        integrity = verify_audio_integrity(memo)
        st.caption(
            ("✓ 原音频校验通过" if integrity else "⚠ 原音频完整性无法确认")
            + f" · memo id: {memo['id']}"
        )
    st.markdown("</div>", unsafe_allow_html=True)


def _selected_audio(recorded: Any, uploaded: Any) -> Any:
    if recorded is not None:
        if uploaded is not None:
            st.info("已同时检测到录音和上传文件，本次优先使用刚录制的音频。")
        return recorded
    return uploaded


def render_record_page(paths: StoragePaths, gateway: Any) -> None:
    st.markdown(
        """
        <div class="memo-hero">
          <div class="memo-kicker">Capture</div>
          <h1>记下此刻，保留变化。</h1>
          <p>原始音频和原始转写始终只读；AI 只负责整理和提出关系候选。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    widget_version = st.session_state.setdefault("record_widget_version", 0)
    location = st.text_input(
        "地点（可选）",
        placeholder="例如：家里",
        key=f"location-{widget_version}",
    )
    recorded = st.audio_input("录一段语音", key=f"audio-input-{widget_version}")
    uploaded = st.file_uploader(
        "或者上传已有音频",
        type=["wav", "mp3", "webm", "ogg"],
        key=f"audio-upload-{widget_version}",
    )
    audio_source = _selected_audio(recorded, uploaded)
    st.caption(
        "音频会发送至阿里云百炼，由千问完成真实语音识别。Base64 内联编码后必须小于 10 MB。"
    )

    can_submit = audio_source is not None
    if st.button(
        "整理并保存",
        type="primary",
        use_container_width=True,
        disabled=not can_submit,
    ):
        audio_bytes = audio_source.getvalue()
        mime_type = getattr(audio_source, "type", None) or "audio/wav"
        original_name = getattr(audio_source, "name", None)
        fingerprint = hashlib.sha256(
            audio_bytes
            + location.encode("utf-8")
        ).hexdigest()
        if st.session_state.get("last_submission_fingerprint") == fingerprint:
            st.info("这次提交已经处理过，已直接显示原结果。")
        else:
            if (
                st.session_state.get("pending_submission_fingerprint") == fingerprint
                and st.session_state.get("pending_operation_id")
            ):
                operation_id = st.session_state.pending_operation_id
            else:
                operation_id = str(uuid4())
                st.session_state.pending_submission_fingerprint = fingerprint
            st.session_state.pending_operation_id = operation_id
            try:
                with st.spinner("正在保存原音频、整理内容并检索历史……"):
                    outcome = process_audio(
                        paths,
                        gateway,
                        audio_bytes=audio_bytes,
                        mime_type=mime_type,
                        original_name=original_name,
                        location=location,
                        operation_id=operation_id,
                    )
                st.session_state.last_submission_fingerprint = fingerprint
                st.session_state.last_memo_id = outcome.memo["id"]
                st.session_state.pending_operation_id = None
                st.session_state.pending_submission_fingerprint = None
                st.success("已整理并保存。")
            except ProcessingError as exc:
                st.error(f"处理未完成：{exc}")
                if exc.capture_id:
                    st.caption(f"原始证据已保存，capture id: {exc.capture_id}")

    last_memo_id = st.session_state.get("last_memo_id")
    if last_memo_id:
        memo = db.get_memo(paths.db_path, last_memo_id)
        if memo:
            _render_memo_result(memo)
            if st.button("再记一条"):
                st.session_state.pop("last_memo_id", None)
                st.session_state.pop("last_submission_fingerprint", None)
                st.session_state.pop("pending_operation_id", None)
                st.session_state.pop("pending_submission_fingerprint", None)
                st.session_state.record_widget_version += 1
                st.rerun()


def _date_label(value: str) -> str:
    created = datetime.fromisoformat(value)
    today = datetime.now(created.tzinfo).date() if created.tzinfo else datetime.now().date()
    if created.date() == today:
        return "今天"
    return created.strftime("%Y年%m月%d日")


def render_history_page(paths: StoragePaths) -> None:
    st.markdown(
        '<div class="memo-hero"><div class="memo-kicker">Archive</div><h1>备忘录</h1><p>整理稿可以编辑；原始声音、原始转写和旧版本始终保留。</p></div>',
        unsafe_allow_html=True,
    )
    memos = db.list_memos(paths.db_path)
    if not memos:
        st.info("还没有备忘录，请先去“记录”留下第一条。")
        return

    grouped: Dict[str, list] = {}
    for memo in memos:
        grouped.setdefault(_date_label(memo["created_at"]), []).append(memo)
    for label, items in grouped.items():
        st.subheader(label)
        for memo in items:
            pending = f" · 待审批 {memo['pending_approval_count']}" if memo["pending_approval_count"] else ""
            with st.expander(f"{memo['title']} · {memo['topic']}{pending}"):
                _render_memo_result(memo)
                with st.form(f"edit-{memo['id']}"):
                    st.markdown("##### 编辑当前整理稿")
                    edited_title = st.text_input("标题", value=memo["title"])
                    edited_markdown = st.text_area(
                        "整理稿",
                        value=memo["cleaned_markdown"],
                        height=170,
                    )
                    if st.form_submit_button("保存为新版本"):
                        try:
                            edit_memo(
                                paths,
                                memo["id"],
                                title=edited_title,
                                cleaned_markdown=edited_markdown,
                            )
                            st.success("已保存新版本，原始证据没有变化。")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"保存失败：{exc}")
                if len(memo.get("versions", [])) > 1:
                    st.markdown("##### 版本记录")
                    for version in memo["versions"]:
                        st.caption(
                            f"v{version['version_no']} · {version['change_reason']} · "
                            f"{version['created_at']}"
                        )


def render_approval_page(paths: StoragePaths) -> None:
    relations = db.list_pending_relations(paths.db_path)
    st.markdown(
        f'<div class="memo-hero"><div class="memo-kicker">Review</div><h1>待审批</h1><p>AI 只提出建议。当前共有 {len(relations)} 条关系需要你确认。</p></div>',
        unsafe_allow_html=True,
    )
    if not relations:
        st.success("当前没有待审批关系。")
        return

    for relation in relations:
        with st.container(border=True):
            st.markdown(
                f"**{relation['topic']}** · `{relation['relation_type']}` · "
                f"{relation['rationale']}"
            )
            columns = st.columns(2)
            with columns[0]:
                st.caption("新内容")
                st.markdown(f"#### {relation['new_title']}")
                st.markdown(relation["new_cleaned_markdown"])
                st.caption(relation["new_created_at"])
            with columns[1]:
                st.caption("旧内容")
                st.markdown(f"#### {relation['old_title']}")
                st.markdown(relation["old_cleaned_markdown"])
                st.caption(relation["old_created_at"])
            st.markdown("**发现的差异**")
            for difference in relation["differences"]:
                st.markdown(f"- {difference}")

            with st.form(f"approval-{relation['id']}"):
                decision = st.radio(
                    "你的决定",
                    list(DECISION_LABELS.keys()),
                    format_func=lambda value: DECISION_LABELS[value],
                    horizontal=True,
                )
                merge_draft = st.text_area(
                    "融合草稿（选择 AI 融合时可编辑；AI 不会自动确认）",
                    value=relation.get("merge_draft") or "",
                    height=110,
                )
                submitted = st.form_submit_button("确认决定", type="primary")
                if submitted:
                    try:
                        result = approve_relation(
                            paths,
                            relation["id"],
                            decision,
                            merge_draft=merge_draft,
                        )
                        if result is None:
                            st.info("已保留为待审批，稍后可以继续处理。")
                        else:
                            st.success("审批已保存，所有来源记录仍然保留。")
                            st.rerun()
                    except Exception as exc:
                        st.error(f"审批失败：{exc}")


def render_timeline_page(paths: StoragePaths) -> None:
    topics = db.list_topics(paths.db_path)
    st.markdown(
        '<div class="memo-hero"><div class="memo-kicker">Timeline</div><h1>主题时间线</h1><p>当前观点、历史记录、待审批候选和未采用内容分开呈现。</p></div>',
        unsafe_allow_html=True,
    )
    if not topics:
        st.info("还没有主题。完成第一条记录后，时间线会出现在这里。")
        return
    topic_by_label = {
        f"{topic['domain']} · {topic['name']}（{topic['event_count']}）": topic
        for topic in topics
    }
    selected_label = st.selectbox("选择主题", list(topic_by_label.keys()))
    selected = topic_by_label[selected_label]
    st.markdown('<div class="memo-card">', unsafe_allow_html=True)
    st.caption("当前主题摘要")
    st.markdown(f"### {selected['current_summary'] or '尚无已审批的当前摘要'}")
    st.markdown("</div>", unsafe_allow_html=True)
    newest_first = st.toggle("最新记录在前", value=True)
    events = db.list_timeline(paths.db_path, selected["id"])
    if not newest_first:
        events.reverse()
    for event in events:
        with st.container(border=True):
            columns = st.columns([4, 1])
            with columns[0]:
                st.markdown(f"#### {event['title']}")
            with columns[1]:
                st.markdown(_status_badge(event["status"]), unsafe_allow_html=True)
            st.write(event["one_line_view"])
            st.caption(event["one_line_change"])
            st.caption(
                f"{event['occurred_at']} · 来源 memo: "
                + ", ".join(event["source_memo_ids"])
            )


def main() -> None:
    _load_css()
    try:
        gateway = _gateway()
    except AIError as exc:
        st.error(str(exc))
        st.info("请在服务器环境变量或 Streamlit Secrets 中配置 DASHSCOPE_API_KEY。")
        st.stop()
    try:
        paths = _storage_paths()
    except ProcessingError as exc:
        st.error(f"存储初始化失败：{exc}")
        st.stop()

    _render_header()
    if (_secret("DEMO_STORAGE_MODE", "session") or "session").lower() != "persistent":
        st.caption("公开演示保护：数据仅属于当前浏览器会话，刷新、冷启动或重新部署后可能丢失。请勿上传敏感录音。")

    page = st.radio(
        "主导航",
        ["🎙️ 记录", "🗂️ 备忘录", "✅ 待审批", "🕰️ 时间线"],
        horizontal=True,
        label_visibility="collapsed",
    )
    if page == "🎙️ 记录":
        render_record_page(paths, gateway)
    elif page == "🗂️ 备忘录":
        render_history_page(paths)
    elif page == "✅ 待审批":
        render_approval_page(paths)
    else:
        render_timeline_page(paths)

    with st.sidebar:
        st.markdown("### 关于这个 Demo")
        st.write("本副本不会读取原 React/FastAPI 项目的数据库或音频。")
        st.write("不包含问答、TTS、登录和多人长期存储。")
        st.caption("模型服务：阿里云百炼 · 千问真实模式")
        st.caption(
            f"ASR: {QWEN_ASR_MODEL}\n\n整理/关系: {QWEN_TEXT_MODEL}\n\n"
            f"向量: {QWEN_EMBEDDING_MODEL}"
        )


if __name__ == "__main__":
    main()
