export function reconcileScatterAxes(availableParams, currentX = "", currentY = "") {
  const params = Array.isArray(availableParams) ? availableParams : [];
  const keys = params
    .map((param) => (param && typeof param.key === "string" ? param.key : ""))
    .filter(Boolean);
  const availableKeys = new Set(keys);

  if (availableKeys.has(currentX) && availableKeys.has(currentY)) {
    return { xAxis: currentX, yAxis: currentY };
  }
  if (keys.length >= 2) {
    return { xAxis: keys[0], yAxis: keys[1] };
  }
  return { xAxis: "", yAxis: "" };
}
