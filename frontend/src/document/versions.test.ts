import { describe, expect, it } from "vitest";

import type { DiffView, VersionSummaryView } from "../api/types";
import { diffCounts, diffHeadline, formatTime, pickPair, versionLabel } from "./versions";

const version = {
  version_no: 7,
  trigger: "manual",
  trigger_label: "手工编辑",
  summary: "改一条：导出文件仅含订单号与金额",
  created_at: "2026-09-24T04:31:00+00:00",
  changed_block_ids: ["feat-order-export.acceptance"],
} as VersionSummaryView;

const diff = {
  from_version_no: 3,
  to_version_no: 5,
  groups: [
    {
      target_label: "5. 功能需求 · order-export · 验收标准",
      added: [{ entry_id: "en1", text: "AC-1", refs: ["E4"] }],
      removed: [],
      changed: [
        {
          before: { entry_id: "en2", text: "手机号脱敏", refs: ["E12"] },
          after: { entry_id: "en2", text: "仅含订单号与金额", refs: ["E12", "E31"] },
        },
      ],
    },
  ],
} as DiffView;

describe("versionLabel", () => {
  it("版本号、触发方式与时间一句话说完", () => {
    expect(versionLabel(version)).toContain("v7 · 手工编辑");
  });

  it("时间按本地时分显示；解析不了就照抄", () => {
    expect(formatTime("2026-09-24T04:31:00+00:00")).toMatch(/^\d{2}:\d{2}$/);
    expect(formatTime("不是时间")).toBe("不是时间");
  });
});

describe("pickPair", () => {
  it("点中间的某一版：它当 from，to 不动", () => {
    expect(pickPair([3, 5], 4, 7)).toEqual([4, 5]);
  });

  it("点比 to 还新的一版：排序不乱（永远是 from < to）", () => {
    expect(pickPair([3, 5], 7, 9)).toEqual([5, 7]);
  });

  it("点已经选中的那个就不动", () => {
    expect(pickPair([3, 5], 5, 7)).toEqual([3, 5]);
  });
});

describe("diffHeadline", () => {
  it("数出新增/改写/删除三条", () => {
    expect(diffCounts(diff)).toEqual({ added: 1, removed: 0, changed: 1 });
    expect(diffHeadline(diff)).toBe("v3 → v5：新增 1 条 · 改写 1 条 · 删除 0 条");
  });

  it("没变化就说没变化", () => {
    const empty = { ...diff, groups: [] } as DiffView;
    expect(diffHeadline(empty)).toBe("v3 和 v5 内容一样");
  });
});
