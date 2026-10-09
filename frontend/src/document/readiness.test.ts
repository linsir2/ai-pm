import { describe, expect, it } from "vitest";

import {
  readinessBySection,
  readinessHeadline,
  readinessItemsFor,
  readinessPercent,
  readinessSummary,
} from "./readiness";
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

describe("readinessPercent", () => {
  const sections = [
    { section_id: "s1" },
    { section_id: "s2" },
    { section_id: "s6" },
    { section_id: "s13" }, // 不参与判定
  ];

  it("无阻塞章节占比：s1 有阻塞，s2/s6 干净 → 2/3", () => {
    // items 里 s1 有两条阻塞、s6 只有一条提示（提示不算阻塞）
    expect(readinessPercent(items, sections)).toBe(67);
  });

  it("s13 不参与：即使只有 s13 干净也不拉低分母", () => {
    expect(readinessPercent([], sections)).toBe(100);
  });

  it("没有可判章节就是 0", () => {
    expect(readinessPercent([], [])).toBe(0);
    expect(readinessPercent([], [{ section_id: "s13" }])).toBe(0);
  });

  it("全都有阻塞就是 0", () => {
    const allBlocked = [
      { ...items[0]!, section_id: "s1" },
      { ...items[0]!, section_id: "s2" },
      { ...items[0]!, section_id: "s6" },
    ];
    expect(readinessPercent(allBlocked, sections)).toBe(0);
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

  it("按章分组：只留有项的章，顺序跟着章节表走（P29 F6）", () => {
    const sections = [
      { section_id: "s1", title: "背景与问题" },
      { section_id: "s2", title: "目标与非目标" },   // 这一章没有缺口：不该出现在清单里
      { section_id: "s6", title: "业务流程与交互" },
    ];

    const groups = readinessBySection([items[0]!, items[2]!], sections);

    expect(groups.map((group) => group.sectionId)).toEqual(["s1", "s6"]);
    expect(groups[0]!.title).toBe("背景与问题");
    expect(groups[0]!.blockers).toBe(1);
    expect(groups[1]!.blockers).toBe(0);        // 提示不算阻塞
  });

  it("没有缺口就是空分组（抽屉里那句「没有缺口」由组件说）", () => {
    expect(readinessBySection([], [{ section_id: "s1", title: "背景与问题" }])).toEqual([]);
  });
});
