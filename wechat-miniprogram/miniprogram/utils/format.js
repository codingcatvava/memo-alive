function pad(value) {
  return String(value).padStart(2, "0");
}

function asDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? new Date() : date;
}

function dayKey(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function groupLabel(value) {
  const date = asDate(value);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (dayKey(date) === dayKey(today)) return "今天";
  if (dayKey(date) === dayKey(yesterday)) return "昨天";
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

function clock(value) {
  const date = asDate(value);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function dateTime(value) {
  const date = asDate(value);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function duration(seconds) {
  return `${pad(Math.floor(seconds / 60))}:${pad(seconds % 60)}`;
}

function shortId(value) {
  return String(value || "").slice(0, 8);
}

module.exports = { groupLabel, clock, dateTime, duration, shortId };
