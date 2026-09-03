const api = require("../../utils/api");
const { duration } = require("../../utils/format");

const recorderManager = wx.getRecorderManager();
const MAX_STORED_AUDIO_BYTES = 50 * 1024 * 1024;
const MAX_REAL_ASR_AUDIO_BYTES = 7 * 1024 * 1024;

Page({
  data: {
    runtime: null,
    stage: "idle",
    recording: false,
    durationSeconds: 0,
    durationText: "00:00",
    filePath: "",
    fileName: "",
    fileSize: 0,
    location: "",
    uploadProgress: 0,
    capture: null,
    manualTranscript: "",
    manualReady: false,
    waveBars: [
      { id: "wave-0", className: "bar-0" },
      { id: "wave-1", className: "bar-1" },
      { id: "wave-2", className: "bar-2" },
      { id: "wave-3", className: "bar-3" },
      { id: "wave-4", className: "bar-2" },
      { id: "wave-5", className: "bar-1" },
      { id: "wave-6", className: "bar-0" },
      { id: "wave-7", className: "bar-2" },
      { id: "wave-8", className: "bar-3" },
      { id: "wave-9", className: "bar-1" },
      { id: "wave-10", className: "bar-2" },
      { id: "wave-11", className: "bar-0" },
    ],
    result: null,
    currentVersion: null,
    error: "",
    needPermissionSettings: false,
  },

  onLoad() {
    this._pageAlive = true;
    this._handleRecorderStart = () => {
      this._recordStartPending = false;
      if (!this._pageAlive) return;
      this.setData({ recording: true, stage: "recording", error: "" });
      this.startTimer();
      if (!this._pageVisible) recorderManager.stop();
    };
    this._handleRecorderStop = (result) => {
      this._recordStartPending = false;
      this.stopTimer();
      if (!this._pageAlive || this._discardNextRecording) {
        this._discardNextRecording = false;
        return;
      }
      const seconds = Math.max(1, Math.round((result.duration || this.data.durationSeconds * 1000) / 1000));
      this.setData({
        recording: false,
        stage: "ready",
        filePath: result.tempFilePath,
        fileName: `memo-${Date.now()}.mp3`,
        fileSize: result.fileSize || 0,
        durationSeconds: seconds,
        durationText: duration(seconds),
      });
    };
    this._handleRecorderError = (error) => {
      this._recordStartPending = false;
      this.stopTimer();
      if (!this._pageAlive) return;
      const message = error.errMsg || "录音失败，请检查麦克风权限";
      this.setData({
        recording: false,
        stage: "idle",
        error: message,
        needPermissionSettings: /auth|authorize|permission|denied/i.test(message),
      });
    };
    recorderManager.onStart(this._handleRecorderStart);
    recorderManager.onStop(this._handleRecorderStop);
    recorderManager.onError(this._handleRecorderError);
  },

  onShow() {
    this._pageVisible = true;
    api.runtime()
      .then((runtime) => this.setData({ runtime }))
      .catch((error) => this.setData({ error: `无法连接后端：${error.message}` }));
  },

  onHide() {
    this._pageVisible = false;
    if (this.data.recording || this._recordStartPending) recorderManager.stop();
  },

  onUnload() {
    this._pageAlive = false;
    this._pageVisible = false;
    if (this.data.recording || this._recordStartPending) {
      this._discardNextRecording = true;
      recorderManager.stop();
    }
    if (typeof recorderManager.offStart === "function") recorderManager.offStart(this._handleRecorderStart);
    if (typeof recorderManager.offStop === "function") recorderManager.offStop(this._handleRecorderStop);
    if (typeof recorderManager.offError === "function") recorderManager.offError(this._handleRecorderError);
    this.stopTimer();
    if (this._audio) this._audio.destroy();
  },

  startTimer() {
    this.stopTimer();
    this._timer = setInterval(() => {
      const next = this.data.durationSeconds + 1;
      this.setData({ durationSeconds: next, durationText: duration(next) });
    }, 1000);
  },

  stopTimer() {
    if (this._timer) clearInterval(this._timer);
    this._timer = null;
  },

  startRecording() {
    this.setData({
      error: "",
      needPermissionSettings: false,
      filePath: "",
      fileName: "",
      fileSize: 0,
      durationSeconds: 0,
      durationText: "00:00",
      result: null,
      currentVersion: null,
    });
    try {
      this._recordStartPending = true;
      recorderManager.start({
        duration: 300000,
        sampleRate: 16000,
        numberOfChannels: 1,
        encodeBitRate: 48000,
        format: "mp3",
        frameSize: 50,
      });
    } catch (error) {
      this._recordStartPending = false;
      this.setData({ error: error.message || "无法启动录音" });
    }
  },

  stopRecording() {
    recorderManager.stop();
  },

  cancelRecording() {
    this._discardNextRecording = true;
    recorderManager.stop();
    this.stopTimer();
    this.setData({
      recording: false,
      stage: "idle",
      durationSeconds: 0,
      durationText: "00:00",
    });
  },

  openPermissionSettings() {
    wx.openSetting();
  },

  chooseAudio() {
    wx.chooseMessageFile({
      count: 1,
      type: "file",
      extension: ["mp3", "wav", "m4a", "aac", "ogg", "webm"],
      success: (result) => {
        const file = result.tempFiles && result.tempFiles[0];
        if (!file) return;
        if (file.size > MAX_STORED_AUDIO_BYTES) {
          this.setData({ error: "音频超过后端 50 MB 限制" });
          return;
        }
        if (this.data.runtime && this.data.runtime.mode === "real" && file.size > MAX_REAL_ASR_AUDIO_BYTES) {
          this.setData({ error: "真实百炼模式请选择小于 7 MB 的音频，避免 Base64 后超过 10 MB ASR 限制" });
          return;
        }
        this.setData({
          stage: "ready",
          filePath: file.path,
          fileName: file.name || "已选择音频",
          fileSize: file.size || 0,
          durationSeconds: 0,
          durationText: "--:--",
          error: "",
        });
      },
      fail: (error) => {
        if (!/cancel/i.test(error.errMsg || "")) {
          this.setData({ error: error.errMsg || "选择音频失败" });
        }
      },
    });
  },

  setLocation(event) {
    this.setData({ location: event.detail.value });
  },

  setManualTranscript(event) {
    const manualTranscript = event.detail.value;
    this.setData({ manualTranscript, manualReady: Boolean(manualTranscript.trim()) });
  },

  playFile() {
    if (!this.data.filePath) return;
    this.playAudio(this.data.filePath);
  },

  playResultAudio() {
    if (!this.data.result) return;
    this.playAudio(api.audioUrl(this.data.result.audio_url));
  },

  playAudio(source) {
    if (this._audio) this._audio.destroy();
    this._audio = wx.createInnerAudioContext();
    this._audio.src = source;
    this._audio.onError((error) => {
      this.setData({ error: error.errMsg || "音频播放失败" });
    });
    this._audio.play();
  },

  async uploadAndProcess() {
    if (!this.data.filePath) return;
    let runtime;
    try {
      runtime = await api.runtime();
      this.setData({ runtime });
    } catch (error) {
      this.setData({ error: `无法确认后端运行模式：${error.message}` });
      return;
    }
    if (runtime.mode === "real" && this.data.fileSize > MAX_REAL_ASR_AUDIO_BYTES) {
      this.setData({ error: "真实百炼模式的音频需小于 7 MB" });
      return;
    }
    this.setData({ stage: "uploading", uploadProgress: 0, error: "" });
    try {
      const capture = await api.uploadAudio(
        this.data.filePath,
        new Date().toISOString(),
        this.data.location,
        (uploadProgress) => this.setData({ uploadProgress }),
      );
      this.setData({ capture });
      if (capture.runtime_mode === "mock") {
        this.setData({ stage: "mock_input" });
        return;
      }
      await this.processCapture(capture.id);
    } catch (error) {
      this.setData({ stage: "failed", error: error.message || "上传失败" });
    }
  },

  async submitMock(event) {
    const source = event.currentTarget.dataset.source;
    const transcript = source === "manual" ? this.data.manualTranscript.trim() : undefined;
    if (source === "manual" && !transcript) return;
    try {
      this.setData({ error: "", stage: "processing" });
      await api.mockTranscript(this.data.capture.id, source, transcript);
      await this.processCapture(this.data.capture.id);
    } catch (error) {
      this.setData({ stage: "mock_input", error: error.message || "转写提交失败，请确认内容后重试" });
    }
  },

  async processCapture(captureId) {
    this.setData({ stage: "processing", error: "" });
    try {
      const result = await api.processCapture(captureId);
      const currentVersion = (result.versions || []).find((item) => item.id === result.current_version_id)
        || (result.versions || [])[0]
        || null;
      this.setData({ result, currentVersion, stage: "done" });
      wx.showToast({ title: "整理完成", icon: "success" });
    } catch (error) {
      this.setData({ stage: "failed", error: error.message || "整理失败" });
    }
  },

  retryProcess() {
    if (this.data.capture && this.data.capture.id) {
      this.processCapture(this.data.capture.id);
    }
  },

  viewMemo() {
    if (!this.data.result) return;
    wx.navigateTo({ url: `/pages/memo/index?id=${this.data.result.id}` });
  },

  openApprovals() {
    if (!this.data.result) return;
    getApp().globalData.approvalMemoId = this.data.result.id;
    wx.switchTab({ url: "/pages/approvals/index" });
  },

  reset() {
    if (this._audio) this._audio.stop();
    this.setData({
      stage: "idle",
      recording: false,
      durationSeconds: 0,
      durationText: "00:00",
      filePath: "",
      fileName: "",
      fileSize: 0,
      uploadProgress: 0,
      capture: null,
      manualTranscript: "",
      manualReady: false,
      result: null,
      currentVersion: null,
      error: "",
    });
  },
});
