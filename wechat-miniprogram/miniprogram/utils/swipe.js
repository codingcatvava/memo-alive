const SWIPE_THRESHOLD = 42;

function getSwipeAction(startPoint, endPoint) {
  if (!startPoint || !endPoint) return "none";

  const deltaX = endPoint.clientX - startPoint.clientX;
  const deltaY = endPoint.clientY - startPoint.clientY;
  if (Math.abs(deltaX) < SWIPE_THRESHOLD || Math.abs(deltaX) <= Math.abs(deltaY)) {
    return "none";
  }
  return deltaX < 0 ? "open" : "close";
}

module.exports = {
  SWIPE_THRESHOLD,
  getSwipeAction,
};
