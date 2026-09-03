const api = require("./utils/api");

App({
  onLaunch() {
    if (!wx.getStorageSync(api.API_BASE_STORAGE_KEY)) {
      wx.setStorageSync(api.API_BASE_STORAGE_KEY, api.DEFAULT_API_BASE);
    }
  },

  globalData: {
    approvalMemoId: "",
  },
});
