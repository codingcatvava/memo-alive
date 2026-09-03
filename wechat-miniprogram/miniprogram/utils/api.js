const DEFAULT_API_BASE = "http://127.0.0.1:8000";
const API_BASE_STORAGE_KEY = "memo_alive_api_base";
const NETWORK_TIMEOUT = 60000;
const POLL_INTERVAL = 2000;

function normalizeBaseUrl(value) {
  const normalized = String(value || "").trim().replace(/\/+$/, "");
  if (!/^https?:\/\/[^\s]+$/i.test(normalized)) {
    throw new Error("后端地址必须以 http:// 或 https:// 开头");
  }
  return normalized;
}

function getBaseUrl() {
  const stored = wx.getStorageSync(API_BASE_STORAGE_KEY) || DEFAULT_API_BASE;
  try {
    return normalizeBaseUrl(stored);
  } catch (_error) {
    return DEFAULT_API_BASE;
  }
}

function setBaseUrl(value) {
  const normalized = normalizeBaseUrl(value);
  wx.setStorageSync(API_BASE_STORAGE_KEY, normalized);
  return normalized;
}

function parsePayload(value) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch (_error) {
    return value;
  }
}

function apiError(response) {
  const payload = parsePayload(response.data) || {};
  const detail = payload.detail;
  const validationMessage = Array.isArray(detail)
    ? detail.map((item) => item.msg || "参数无效").join("；")
    : "";
  const message = typeof detail === "string"
    ? detail
    : validationMessage || (detail && detail.message) || response.errMsg || `请求失败 (${response.statusCode})`;
  const error = new Error(message);
  error.code = detail && detail.code ? detail.code : "API_ERROR";
  error.status = response.statusCode || 0;
  return error;
}

function request(path, options) {
  const config = options || {};
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${getBaseUrl()}${path}`,
      method: config.method || "GET",
      data: config.data,
      header: config.header || { "Content-Type": "application/json" },
      timeout: Math.min(config.timeout || NETWORK_TIMEOUT, NETWORK_TIMEOUT),
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(parsePayload(response.data));
          return;
        }
        reject(apiError(response));
      },
      fail(reason) {
        reject(new Error(reason.errMsg || "无法连接后端"));
      },
    });
  });
}

function uploadAudio(filePath, recordedAt, location, onProgress) {
  return new Promise((resolve, reject) => {
    const formData = { recorded_at: recordedAt };
    if (String(location || "").trim()) {
      formData.location_text = String(location).trim();
    }
    const task = wx.uploadFile({
      url: `${getBaseUrl()}/api/v1/captures`,
      filePath,
      name: "audio",
      formData,
      timeout: NETWORK_TIMEOUT,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(parsePayload(response.data));
          return;
        }
        reject(apiError(response));
      },
      fail(reason) {
        reject(new Error(reason.errMsg || "音频上传失败"));
      },
    });
    if (task && typeof task.onProgressUpdate === "function" && onProgress) {
      task.onProgressUpdate((event) => onProgress(event.progress || 0));
    }
  });
}

function idempotencyKey() {
  return `wx-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function isTimeout(error) {
  return /timeout|超时/i.test((error && error.message) || "");
}

async function waitForProcessedCapture(captureId) {
  for (let attempt = 0; attempt < 150; attempt += 1) {
    const capture = await request(`/api/v1/captures/${captureId}`, { timeout: 10000 });
    if (capture.memo_id && capture.current_version_id) {
      return request(`/api/v1/memos/${capture.memo_id}`);
    }
    if (capture.memo_status === "failed" || (capture.status === "failed" && !capture.memo_id)) {
      throw new Error(`后端处理失败：${capture.last_error_code || "请在后端日志中查看原因"}`);
    }
    await delay(POLL_INTERVAL);
  }
  throw new Error("后端仍在处理这条录音，请稍后点击重试；小程序不会重复提交处理任务");
}

async function processCapture(captureId) {
  const existing = await request(`/api/v1/captures/${captureId}`, { timeout: 10000 });
  if (existing.memo_id && existing.current_version_id) {
    return request(`/api/v1/memos/${existing.memo_id}`);
  }
  if (existing.memo_status === "processing" || existing.status === "transcribing") {
    return waitForProcessedCapture(captureId);
  }
  try {
    return await request(`/api/v1/captures/${captureId}/process`, {
      method: "POST",
      timeout: NETWORK_TIMEOUT,
    });
  } catch (error) {
    if (!isTimeout(error)) throw error;
    return waitForProcessedCapture(captureId);
  }
}

async function editMemo(id, title, cleanedMarkdown) {
  try {
    return await request(`/api/v1/memos/${id}`, {
      method: "PUT",
      data: { title, cleaned_markdown: cleanedMarkdown },
      timeout: NETWORK_TIMEOUT,
    });
  } catch (error) {
    if (!isTimeout(error)) throw error;
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const memo = await request(`/api/v1/memos/${id}`, { timeout: 10000 });
      const current = (memo.versions || []).find((item) => item.id === memo.current_version_id);
      if (
        current
        && current.title === title
        && current.cleaned_markdown === cleanedMarkdown
      ) return memo;
      await delay(POLL_INTERVAL);
    }
    throw new Error("编辑仍在后端处理中，请返回详情页刷新；小程序不会重复创建版本");
  }
}

const TERMINAL_APPROVAL_STATUSES = [
  "approved_supplement",
  "rejected_supplement",
  "chose_new",
  "chose_old",
  "no_action",
];

async function waitForApproval(relationId) {
  for (let attempt = 0; attempt < 120; attempt += 1) {
    const relation = await request(`/api/v1/approvals/${relationId}`, { timeout: 10000 });
    if (TERMINAL_APPROVAL_STATUSES.includes(relation.status)) return relation;
    if (relation.status === "rewrite_failed") {
      throw new Error("审批决定已保存，但 AI 改写失败；可使用同一选择安全重试");
    }
    await delay(POLL_INTERVAL);
  }
  throw new Error("审批仍在后端处理中，请稍后返回本页刷新；不会重复执行决定");
}

async function submitApproval(path, relationId, data, key) {
  try {
    const result = await request(path, {
      method: "POST",
      header: {
        "Content-Type": "application/json",
        "Idempotency-Key": key || idempotencyKey(),
      },
      data,
      timeout: NETWORK_TIMEOUT,
    });
    return result.status === "decision_processing"
      ? waitForApproval(relationId)
      : result;
  } catch (error) {
    if (!isTimeout(error)) throw error;
    return waitForApproval(relationId);
  }
}

function audioUrl(path) {
  if (/^https?:\/\//i.test(path || "")) return path;
  return `${getBaseUrl()}${path || ""}`;
}

module.exports = {
  DEFAULT_API_BASE,
  API_BASE_STORAGE_KEY,
  normalizeBaseUrl,
  getBaseUrl,
  setBaseUrl,
  request,
  uploadAudio,
  audioUrl,
  newIdempotencyKey: idempotencyKey,
  runtime: () => request("/api/v1/runtime"),
  history: async () => (await request("/api/v1/memos/history")).items || [],
  memo: (id) => request(`/api/v1/memos/${id}`),
  editMemo,
  deleteMemo: (id) => request(`/api/v1/memos/${id}`, { method: "DELETE" }),
  mockTranscript: (captureId, source, transcript) => request(`/api/v1/captures/${captureId}/mock-transcript`, {
    method: "POST",
    data: { source, transcript },
  }),
  capture: (captureId) => request(`/api/v1/captures/${captureId}`),
  processCapture,
  messages: async () => (await request("/api/v1/approval-messages")).items || [],
  cards: async (memoId) => (await request(`/api/v1/approval-messages/${memoId}/cards`)).items || [],
  allCards: async () => (await request("/api/v1/approvals")).items || [],
  supplement: (relationId, baseChoice, key) => submitApproval(
    `/api/v1/approvals/${relationId}/supplement`,
    relationId,
    { approved: true, base_choice: baseChoice },
    key,
  ),
  conflict: (relationId, choice, key) => submitApproval(
    `/api/v1/approvals/${relationId}/conflict`,
    relationId,
    { choice },
    key,
  ),
  topics: async () => (await request("/api/v1/topics")).items || [],
  timeline: async (topicId) => (await request(`/api/v1/topics/${topicId}/timeline`)).items || [],
};
