const api = require("../../utils/api");
const { dateTime } = require("../../utils/format");

function prepareMemo(memo) {
  const versions = (memo.versions || []).map((version) => ({
    ...version,
    createdAtText: dateTime(version.created_at),
    isCurrent: version.id === memo.current_version_id,
  }));
  const current = versions.find((version) => version.isCurrent) || versions[0] || {
    id: "",
    version_no: "-",
    title: "处理中的备忘录",
    summary: "",
    cleaned_markdown: "整理稿尚未生成。",
  };
  return {
    memo: { ...memo, versions },
    current,
    versions,
    recordedAtText: dateTime(memo.recorded_at),
  };
}

Page({
  data: {
    memo: null,
    current: null,
    versions: [],
    recordedAtText: "",
    loading: true,
    error: "",
    editing: false,
    saving: false,
    titleDraft: "",
    markdownDraft: "",
    audioPlaying: false,
    audioLoading: false,
    audioError: "",
  },

  onLoad(options) {
    this.memoId = String((options && options.id) || "");
    this.audioContext = null;
    this.audioSource = "";
    if (!this.memoId) {
      this.setData({ loading: false, error: "缺少备忘录 ID，无法加载详情。" });
      return;
    }
    this.load();
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh());
  },

  onUnload() {
    if (this.audioContext) {
      this.audioContext.stop();
      this.audioContext.destroy();
      this.audioContext = null;
    }
  },

  async load() {
    this.setData({ loading: true, error: "" });
    try {
      const memo = await api.memo(this.memoId);
      this.applyMemo(memo);
    } catch (error) {
      this.setData({ error: error.message || "备忘录加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  applyMemo(memo) {
    const prepared = prepareMemo(memo);
    this.setData({
      ...prepared,
      editing: false,
      saving: false,
      titleDraft: prepared.current.id ? prepared.current.title : "",
      markdownDraft: prepared.current.id ? prepared.current.cleaned_markdown : "",
    });
  },

  ensureAudioContext() {
    if (this.audioContext) return this.audioContext;
    const audio = wx.createInnerAudioContext();
    audio.autoplay = false;
    audio.onWaiting(() => this.setData({ audioLoading: true }));
    audio.onCanplay(() => this.setData({ audioLoading: false }));
    audio.onPlay(() => this.setData({ audioPlaying: true, audioLoading: false, audioError: "" }));
    audio.onPause(() => this.setData({ audioPlaying: false, audioLoading: false }));
    audio.onStop(() => this.setData({ audioPlaying: false, audioLoading: false }));
    audio.onEnded(() => this.setData({ audioPlaying: false, audioLoading: false }));
    audio.onError((reason) => {
      this.setData({
        audioPlaying: false,
        audioLoading: false,
        audioError: (reason && reason.errMsg) || "原始音频播放失败",
      });
    });
    this.audioContext = audio;
    return audio;
  },

  toggleAudio() {
    const memo = this.data.memo;
    if (!memo || !memo.audio_url) return;
    const audio = this.ensureAudioContext();
    if (this.data.audioPlaying) {
      audio.pause();
      return;
    }
    const source = api.audioUrl(memo.audio_url);
    if (this.audioSource !== source) {
      this.audioSource = source;
      audio.src = source;
    }
    this.setData({ audioLoading: true, audioError: "" });
    audio.play();
  },

  beginEdit() {
    const current = this.data.current;
    if (!current || !current.id) return;
    this.setData({
      editing: true,
      error: "",
      titleDraft: current.title || "",
      markdownDraft: current.cleaned_markdown || "",
    });
  },

  cancelEdit() {
    const current = this.data.current;
    this.setData({
      editing: false,
      titleDraft: current ? current.title : "",
      markdownDraft: current ? current.cleaned_markdown : "",
    });
  },

  onTitleInput(event) {
    this.setData({ titleDraft: event.detail.value });
  },

  onMarkdownInput(event) {
    this.setData({ markdownDraft: event.detail.value });
  },

  async saveEdit() {
    const title = String(this.data.titleDraft || "").trim();
    const markdown = String(this.data.markdownDraft || "").trim();
    if (!title || !markdown) {
      wx.showToast({ title: "标题和整理稿不能为空", icon: "none" });
      return;
    }
    this.setData({ saving: true, error: "" });
    try {
      const memo = await api.editMemo(this.memoId, title, markdown);
      this.applyMemo(memo);
      wx.showToast({ title: "已保存新版本", icon: "success" });
    } catch (error) {
      this.setData({ saving: false, error: error.message || "整理稿保存失败" });
    }
  },

  openApproval() {
    if (!this.data.memo || !this.data.memo.pending_approval_count) return;
    getApp().globalData.approvalMemoId = this.memoId;
    wx.switchTab({ url: "/pages/approvals/index" });
  },
});
