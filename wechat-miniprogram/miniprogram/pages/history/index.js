const api = require("../../utils/api");
const { groupLabel, clock } = require("../../utils/format");
const { getSwipeAction } = require("../../utils/swipe");

function statusText(item) {
  if (item.pending_approval_count) return `待审批 ${item.pending_approval_count} 条`;
  if (item.status === "failed") return "处理失败";
  if (item.status === "processing") return "处理中";
  return "已完成";
}

function statusClass(item) {
  if (item.pending_approval_count) return "pending";
  if (item.status === "failed") return "failed";
  if (item.status === "processing") return "processing";
  return "complete";
}

function groupItems(items) {
  const groups = [];
  const positions = {};
  items.forEach((item) => {
    const label = groupLabel(item.recorded_at);
    if (positions[label] === undefined) {
      positions[label] = groups.length;
      groups.push({ label, items: [] });
    }
    groups[positions[label]].items.push({
      ...item,
      timeText: clock(item.recorded_at),
      statusText: statusText(item),
      statusClass: statusClass(item),
      topicText: (item.topics || []).join(" · ") || "主题待生成",
    });
  });
  return groups;
}

Page({
  data: {
    runtime: null,
    groups: [],
    loading: true,
    error: "",
    openMemoId: "",
    deletingMemoId: "",
  },

  onLoad() {
    this.memoTouch = null;
    this.suppressMemoTap = false;
  },

  onUnload() {
    if (this.memoTapTimer) clearTimeout(this.memoTapTimer);
  },

  onShow() {
    this.load();
  },

  onPullDownRefresh() {
    this.load().finally(() => wx.stopPullDownRefresh());
  },

  async load() {
    this.setData({ loading: true, error: "", openMemoId: "" });
    try {
      const runtime = await api.runtime();
      const items = await api.history();
      this.setData({ runtime, groups: groupItems(items) });
      this.refreshApprovalBadge();
    } catch (error) {
      this.setData({ error: error.message || "无法连接后端" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async refreshApprovalBadge() {
    try {
      const messages = await api.messages();
      const count = messages.reduce((sum, item) => sum + item.pending_approval_count, 0);
      if (count > 0) {
        wx.setTabBarBadge({ index: 3, text: String(Math.min(count, 99)) });
      } else {
        wx.removeTabBarBadge({ index: 3 });
      }
    } catch (_error) {
      // History remains usable when the badge request fails.
    }
  },

  openMemo(event) {
    if (this.suppressMemoTap) return;
    if (this.data.openMemoId) {
      this.setData({ openMemoId: "" });
      return;
    }
    wx.navigateTo({ url: `/pages/memo/index?id=${event.currentTarget.dataset.id}` });
  },

  openApproval(event) {
    this.setData({ openMemoId: "" });
    getApp().globalData.approvalMemoId = event.currentTarget.dataset.id;
    wx.switchTab({ url: "/pages/approvals/index" });
  },

  onMemoTouchStart(event) {
    const touch = event.touches && event.touches[0];
    if (!touch) return;
    this.memoTouch = {
      id: event.currentTarget.dataset.id,
      start: { clientX: touch.clientX, clientY: touch.clientY },
      end: { clientX: touch.clientX, clientY: touch.clientY },
    };
  },

  onMemoTouchMove(event) {
    if (!this.memoTouch) return;
    const touch = event.touches && event.touches[0];
    if (!touch) return;
    this.memoTouch.end = { clientX: touch.clientX, clientY: touch.clientY };
  },

  onMemoTouchEnd(event) {
    if (!this.memoTouch) return;
    const touch = event.changedTouches && event.changedTouches[0];
    if (touch) {
      this.memoTouch.end = { clientX: touch.clientX, clientY: touch.clientY };
    }

    const { id, start, end } = this.memoTouch;
    const action = getSwipeAction(start, end);
    this.memoTouch = null;
    if (action !== "none") {
      this.suppressMemoTap = true;
      if (this.memoTapTimer) clearTimeout(this.memoTapTimer);
      this.memoTapTimer = setTimeout(() => {
        this.suppressMemoTap = false;
      }, 120);
    }
    if (action === "open") {
      this.setData({ openMemoId: id });
    } else if (action === "close") {
      this.setData({ openMemoId: "" });
    }
  },

  onMemoTouchCancel() {
    this.memoTouch = null;
  },

  async deleteMemo(event) {
    const id = event.currentTarget.dataset.id;
    if (!id || this.data.deletingMemoId) return;

    this.setData({ deletingMemoId: id });
    try {
      await api.deleteMemo(id);
      wx.showToast({ title: "已删除", icon: "success" });
      await this.load();
    } catch (error) {
      wx.showModal({ title: "删除失败", content: error.message, showCancel: false });
    } finally {
      this.setData({ deletingMemoId: "" });
    }
  },
});
