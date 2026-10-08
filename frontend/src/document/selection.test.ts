/* 选区把手的定位数学（M13）：居中 / 翻转 / clamp。 */

import { describe, expect, it } from "vitest";

import { handlePlacement } from "./selection";

const column = { left: 100, top: 50, right: 600, bottom: 800 };
const size = { width: 100, height: 28 };

describe("handlePlacement", () => {
  it("常规：居中于选区上方 8px", () => {
    const selection = { left: 300, top: 200, right: 400, bottom: 220 };

    expect(handlePlacement(selection, column, size)).toEqual({ left: 300, top: 164 });
  });

  it("靠左 / 靠右：横向 clamp 在文档栏内（永不越到右栏）", () => {
    const leftEdge = { left: 90, top: 200, right: 110, bottom: 220 };
    expect(handlePlacement(leftEdge, column, size).left).toBe(100);

    const rightEdge = { left: 590, top: 200, right: 620, bottom: 220 };
    expect(handlePlacement(rightEdge, column, size).left).toBe(500);
  });

  it("上方放不下：翻到选区下方 8px", () => {
    const selection = { left: 300, top: 60, right: 400, bottom: 80 };

    expect(handlePlacement(selection, column, size)).toEqual({ left: 300, top: 88 });
  });

  it("选区比栏还宽：也不越界", () => {
    const wide = { left: 50, top: 200, right: 700, bottom: 220 };
    const pos = handlePlacement(wide, column, size);

    expect(pos.left).toBeGreaterThanOrEqual(column.left);
    expect(pos.left + size.width).toBeLessThanOrEqual(column.right);
  });
});
