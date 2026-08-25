import assert from "node:assert/strict";
import test from "node:test";

import { reconcileScatterAxes } from "./report-metadata.mjs";

const params = (keys) => keys.map((key) => ({ key }));

test("preserves axes that are valid for the selected experiment", () => {
  assert.deepEqual(reconcileScatterAxes(params(["x", "y", "z"]), "z", "x"), {
    xAxis: "z",
    yAxis: "x",
  });
});

test("selects the first two parameters when current axes are invalid", () => {
  assert.deepEqual(reconcileScatterAxes(params(["new_x", "new_y"]), "old_x", "old_y"), {
    xAxis: "new_x",
    yAxis: "new_y",
  });
});

test("clears axes when fewer than two parameters are available", () => {
  assert.deepEqual(reconcileScatterAxes(params(["only"]), "old_x", "old_y"), {
    xAxis: "",
    yAxis: "",
  });
});
