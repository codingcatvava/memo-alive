const api = require("../../utils/api");
const { dateTime } = require("../../utils/format");

function modelLines(models) {
  return Object.keys(models || {}).map((role) => ({
    role,
    model: models[role],
  }));
}

Page({
  data: {
    savedBase: "",
    apiBaseDraft: "",
    testing: false,
    testError: "",
    testedAt: "",
    health: null,
    runtime: null,
    modelLines: [],
  },

  onLoad() {
    this.loadBase();
  },

  loadBase() {
    const base = api.getBaseUrl();
    this.setData({ savedBase: base, apiBaseDraft: base });
  },

  onBaseInput(event) {
    this.setData({ apiBaseDraft: event.detail.value, testError: "" });
  },

  saveBase(showToast) {
    try {
      const savedBase = api.setBaseUrl(this.data.apiBaseDraft);
      this.setData({ savedBase, apiBaseDraft: savedBase, testError: "" });
      if (showToast !== false) {
        wx.showToast({ title: "后端地址已保存", icon: "success" });
      }
      return savedBase;
    } catch (error) {
      this.setData({ testError: error.message || "后端地址不正确" });
      return "";
    }
  },

  saveOnly() {
    this.saveBase(true);
  },

  async saveAndTest() {
    if (!this.saveBase(false)) return;
    this.setData({
      testing: true,
      testError: "",
      health: null,
      runtime: null,
      modelLines: [],
    });
    try {
      const results = await Promise.all([
        api.request("/health"),
        api.runtime(),
      ]);
      const health = results[0];
      const runtime = results[1];
      this.setData({
        health,
        runtime,
        modelLines: modelLines(runtime.models),
        testedAt: dateTime(new Date()),
      });
      wx.showToast({ title: "后端已连通", icon: "success" });
    } catch (error) {
      this.setData({
        testError: error.message || "无法连接后端",
        testedAt: dateTime(new Date()),
      });
    } finally {
      this.setData({ testing: false });
    }
  },

  restoreDefault() {
    wx.showModal({
      title: "恢复本地默认地址？",
      content: `将改回 ${api.DEFAULT_API_BASE}，适用于后端运行在当前电脑的开发者工具。`,
      confirmText: "恢复默认",
      success: (result) => {
        if (!result.confirm) return;
        const savedBase = api.setBaseUrl(api.DEFAULT_API_BASE);
        this.setData({
          savedBase,
          apiBaseDraft: savedBase,
          testError: "",
          health: null,
          runtime: null,
          modelLines: [],
          testedAt: "",
        });
        wx.showToast({ title: "已恢复", icon: "success" });
      },
    });
  },

  copyBase() {
    wx.setClipboardData({ data: this.data.savedBase });
  },
});
