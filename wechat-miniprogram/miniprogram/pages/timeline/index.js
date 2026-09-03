const api = require("../../utils/api");
const { dateTime } = require("../../utils/format");

const STATUS_TEXT = {
  current: "当前",
  historical: "历史",
  pending_approval: "待审批",
  rejected: "未采用",
};

function presentEvent(event) {
  return {
    ...event,
    occurredAtText: dateTime(event.occurred_at),
    statusText: STATUS_TEXT[event.status] || event.status || "未知",
  };
}

Page({
  data: {
    topics: [],
    topicNames: [],
    selectedIndex: 0,
    selectedTopic: null,
    events: [],
    ascending: false,
    loading: true,
    error: "",
  },

  onShow() {
    this.loadTopics();
  },

  onPullDownRefresh() {
    this.loadTopics().finally(() => wx.stopPullDownRefresh());
  },

  async loadTopics() {
    this.setData({ loading: true, error: "" });
    try {
      const topics = await api.topics();
      const previousId = this.data.selectedTopic && this.data.selectedTopic.id;
      let selectedIndex = topics.findIndex((topic) => topic.id === previousId);
      if (selectedIndex < 0) selectedIndex = 0;
      const selectedTopic = topics[selectedIndex] || null;
      this.setData({
        topics,
        topicNames: topics.map((topic) => `${topic.name} (${topic.event_count || 0})`),
        selectedIndex,
        selectedTopic,
        events: selectedTopic ? this.data.events : [],
      });
      if (selectedTopic) {
        await this.loadTimeline(selectedTopic.id);
      }
    } catch (error) {
      this.setData({ error: error.message || "时间线加载失败", events: [] });
    } finally {
      this.setData({ loading: false });
    }
  },

  async loadTimeline(topicId) {
    const requestId = (this._timelineRequestId || 0) + 1;
    this._timelineRequestId = requestId;
    const items = await api.timeline(topicId);
    if (
      requestId !== this._timelineRequestId
      || !this.data.selectedTopic
      || this.data.selectedTopic.id !== topicId
    ) return;
    const events = items.map(presentEvent);
    this.setData({
      events: this.data.ascending ? events.reverse() : events,
    });
  },

  async onTopicChange(event) {
    const selectedIndex = Number(event.detail.value);
    const selectedTopic = this.data.topics[selectedIndex];
    if (!selectedTopic) return;
    this.setData({
      selectedIndex,
      selectedTopic,
      loading: true,
      error: "",
      events: [],
    });
    try {
      await this.loadTimeline(selectedTopic.id);
    } catch (error) {
      if (this.data.selectedTopic && this.data.selectedTopic.id === selectedTopic.id) {
        this.setData({ error: error.message || "时间线加载失败" });
      }
    } finally {
      if (this.data.selectedTopic && this.data.selectedTopic.id === selectedTopic.id) {
        this.setData({ loading: false });
      }
    }
  },

  toggleOrder() {
    this.setData({
      ascending: !this.data.ascending,
      events: this.data.events.slice().reverse(),
    });
  },

  retry() {
    this.loadTopics();
  },
});
