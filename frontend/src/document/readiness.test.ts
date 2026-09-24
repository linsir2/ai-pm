import { describe, expect, it } from "vitest";

import { readinessHeadline, readinessItemsFor, readinessSummary } from "./readiness";
import type { ReadinessItemView } from "../api/types";

const items: ReadinessItemView[] = [
  {
    section_id: "s1",
    code: "section_min_entries",
    title: "背景与问题还缺 1 条内容",
    detail: "至少 1 条",
    severity: "blocker",
  },
  {
    section_id: "s1",
    code: "feature_field_empty",
    title: "功能字段还有缺口",
    detail: "order-export：验收标准",
    severity: "blocker",
  },
  {
    section_id: "s6",
    code: "section_kind_present",
    title: "业务流程与交互还没有主流程",
    detail: "用 steps 块写",
    severity: "warn",
  },
];

describe("readinessSummary", () => {
  it("按严重度计数", () => {
    const summary = readinessSummary(items);
    expect(summary.blockers).toBe(2);
    expect(summary.warns).toBe(1);
  });

  it("按章节归拢，顺序不变", () => {
    const summary = readinessSummary(items);
    expect(summary.bySection.get("s1")?.map((item) => item.code)).toEqual([
      "section_min_entries",
      "feature_field_empty",
    ]);
    expect(summary.bySection.get("s6")).toHaveLength(1);
  });

  it("空清单是干净的", () => {
    const summary = readinessSummary([]);
    expect(summary.blockers).toBe(0);
    expect(summary.bySection.size).toBe(0);
  });
});

describe("readinessItemsFor", () => {
  it("只看这一章", () => {
    expect(readinessItemsFor(items, "s6")).toHaveLength(1);
    expect(readinessItemsFor(items, "s2")).toEqual([]);
  });
});

describe("readinessHeadline", () => {
  it("有阻塞就说差几项", () => {
    expect(readinessHeadline(items)).toBe("就差 2 项");
  });

  it("只有提示时说提示", () => {
    expect(readinessHeadline([items[2]!])).toBe("提示 1 项");
  });

  it("没问题就明说就绪", () => {
    expect(readinessHeadline([])).toBe("就绪");
  });
});
