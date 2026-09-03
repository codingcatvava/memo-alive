const api = require("../../utils/api");
const { dateTime } = require("../../utils/format");

const RELATION_TEXT = {
  duplicate: "重复",
  complement: "补充",
  update: "更新",
  conditional: "条件变化",
  conflict: "冲突",
};

function presentMessage(message) {
  return {
    ...message,
    recordedAtText: dateTime(message.recorded_at),
  };
}

function presentCard(card) {
  const isSupplement = card.approval_route === "old_supplements_new";
  const isTopicSupplement = isSupplement && card.relation_scope === "topic_all";
  return {
    ...card,
    isSupplement,
    relationText: RELATION_TEXT[card.semantic_relation] || card.semantic_relation || "待判断",
    routeText: isSupplement ? "选择融合主内容" : "选择要保留的内容",
    newLabel: isTopicSupplement ? "新内容·当前全部事项" : "新内容",
    oldLabel: isTopicSupplement ? "旧内容·可补充事项" : "旧内容",
    newRecordedAtText: dateTime(card.new_recorded_at),
    oldRecordedAtText: dateTime(card.old_recorded_at),
    confirmText: isSupplement ? "确认并融合" : "确认采用",
  };
}

function decisionDescription(card, choice) {
  if (card.isSupplement) {
    return choice === "new"
      ? "以新内容为主，将旧内容中可安全补充的信息融入新版本。"
      : "以旧内容为主，将新内容融入新版本。";
  }
  return choice === "new"
    ? "采用新内容作为当前观点，旧内容仍保留在历史中。"
    : "采用旧内容作为当前观点，新内容仍保留且标记为未采用。";
}

Page({
  data: {
    viewMode: "messages",
    messages: [],
    selectedMemoId: "",
    cards: [],
    currentIndex: 0,
    currentCard: null,
    selection: "",
    loading: true,
    busy: false,
    error: "",
  },

  onLoad() {
    this.decisionKeys = {};
  },

  onShow() {
    const app = getApp();
    const routedMemoId = app.globalData.approvalMemoId || "";
    app.globalData.approvalMemoId = "";
    if (routedMemoId) {
      this.loadCards(routedMemoId);
    } else if (this.data.selectedMemoId) {
      this.loadCards(this.data.selectedMemoId);
    } else {
      this.loadMessages();
    }
  },

  onPullDownRefresh() {
    const task = this.data.selectedMemoId
      ? this.loadCards(this.data.selectedMemoId)
      : this.loadMessages();
    task.finally(() => wx.stopPullDownRefresh());
  },

  updateBadge(messages) {
    const count = messages.reduce(
      (sum, message) => sum + Number(message.pending_approval_count || 0),
      0,
    );
    if (count > 0) {
      wx.setTabBarBadge({ index: 3, text: String(Math.min(count, 99)) });
    } else {
      wx.removeTabBarBadge({ index: 3 });
    }
  },

  async loadMessages() {
    this.setData({ loading: true, error: "", viewMode: "messages" });
    try {
      const messages = (await api.messages()).map(presentMessage);
      this.updateBadge(messages);
      this.setData({
        messages,
        selectedMemoId: "",
        cards: [],
        currentIndex: 0,
        currentCard: null,
        selection: "",
      });
    } catch (error) {
      this.setData({ error: error.message || "待审批消息加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadCards(memoId) {
    this.setData({
      loading: true,
      error: "",
      viewMode: "cards",
      selectedMemoId: memoId,
    });
    try {
      const cards = (await api.cards(memoId)).map(presentCard);
      const currentIndex = Math.min(this.data.currentIndex, Math.max(cards.length - 1, 0));
      this.setData({
        cards,
        currentIndex,
        currentCard: cards[currentIndex] || null,
        selection: "",
      });
      this.refreshBadge();
    } catch (error) {
      this.setData({ error: error.message || "审批内容加载失败" });
    } finally {
      this.setData({ loading: false });
    }
  },

  async refreshBadge() {
    try {
      const messages = (await api.messages()).map(presentMessage);
      this.updateBadge(messages);
    } catch (_error) {
      // The approval card remains usable when only the badge refresh fails.
    }
  },

  openMessage(event) {
    this.setData({ currentIndex: 0 });
    this.loadCards(event.currentTarget.dataset.id);
  },

  backToMessages() {
    this.loadMessages();
  },

  selectEvidence(event) {
    if (this.data.busy) return;
    const selection = event.currentTarget.dataset.choice;
    if (selection !== "new" && selection !== "old") return;
    this.setData({ selection, error: "" });
  },

  previousCard() {
    this.moveCard(-1);
  },

  nextCard() {
    this.moveCard(1);
  },

  moveCard(offset) {
    if (this.data.busy) return;
    const currentIndex = Math.max(
      0,
      Math.min(this.data.cards.length - 1, this.data.currentIndex + offset),
    );
    this.setData({
      currentIndex,
      currentCard: this.data.cards[currentIndex] || null,
      selection: "",
      error: "",
    });
  },

  confirmSelection() {
    const card = this.data.currentCard;
    const selection = this.data.selection;
    if (!card || (selection !== "new" && selection !== "old") || this.data.busy) return;
    const selectedLabel = selection === "new" ? "新内容" : "旧内容";
    wx.showModal({
      title: `确认选择${selectedLabel}？`,
      content: decisionDescription(card, selection),
      confirmText: "确认提交",
      confirmColor: "#143d2f",
      success: (result) => {
        if (result.confirm) this.submitDecision(card, selection);
      },
    });
  },

  async submitDecision(card, selection) {
    this.setData({ busy: true, error: "" });
    const keyName = `${card.id}:${selection}`;
    const idempotencyKey = this.decisionKeys[keyName] || api.newIdempotencyKey();
    this.decisionKeys[keyName] = idempotencyKey;
    try {
      if (card.approval_route === "old_supplements_new") {
        await api.supplement(card.id, selection, idempotencyKey);
      } else if (card.approval_route === "new_old_conflict") {
        await api.conflict(card.id, selection, idempotencyKey);
      } else {
        throw new Error("该关系不支持在小程序中审批");
      }
      delete this.decisionKeys[keyName];
      wx.showToast({ title: "审批已保存", icon: "success" });
    } catch (error) {
      this.setData({ error: error.message || "审批提交失败", busy: false });
      return;
    }
    try {
      const remaining = await api.cards(this.data.selectedMemoId);
      if (remaining.length) {
        await this.loadCards(this.data.selectedMemoId);
      } else {
        await this.loadMessages();
      }
    } catch (error) {
      this.setData({ error: `审批已保存，但列表刷新失败：${error.message || "请下拉刷新"}` });
    } finally {
      this.setData({ busy: false });
    }
  },

  retry() {
    if (this.data.selectedMemoId) {
      this.loadCards(this.data.selectedMemoId);
    } else {
      this.loadMessages();
    }
  },
});
