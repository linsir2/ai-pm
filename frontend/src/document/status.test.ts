import { describe, expect, it } from "vitest";

import { sectionIdOf } from "../labels";
import { filledSectionCount, sectionBadge, sectionStatus } from "./status";
import type { ReadinessItemView } from "../api/types";

const filled = {
  section_id: "s1",
  title: "背景与问题",
  blocks: [{ entries: [{ entry_id: "en1", text: "有内容", refs: ["E1"] }] }],
};
const empty = { section_id: "s9", title: "技术方案与约束", blocks: [] };

describe("sectionStatus", () => {
  it("没有内容也没有开放问题 → empty", () => {
    expect(sectionStatus(empty, null)).toBe("empty");
  });

  it("有内容 → filled", () => {
    expect(sectionStatus(filled, null)).toBe("filled");
  });

  it("空槽不算内容：只有空块还是 empty（s8 六块是骨架）", () => {
    const skeleton = {
      section_id: "s8",
      title: "非功能需求",
      blocks: [{ entries: [] }, { entries: [] }],
    };
    expect(sectionStatus(skeleton, null)).toBe("empty");
    expect(filledSectionCount({ sections: [skeleton, filled] })).toBe(1);
  });

  it("本轮草案里有落在这章的提案 → gap（还要你再确认一次）", () => {
    const round = {
      draft: { proposals: [{ proposal_id: "p1", target_label: "s9", warning: null }] },
    } as never;
    expect(sectionStatus(empty, round)).toBe("gap");
  });

  it("本轮有落在这一章的开放问题 → open", () => {
    const round = {
      questions: [{ question_id: "q1", target_label: "s9", kind: "gap" }],
    } as never;
    expect(sectionStatus(empty, round)).toBe("open");
  });

  it("矛盾题优先于缺口题", () => {
    const round = {
      questions: [
        { question_id: "q1", target_label: "s4", kind: "gap" },
        { question_id: "q2", target_label: "s4", kind: "conflict" },
      ],
    } as never;
    expect(sectionStatus({ ...empty, section_id: "s4" }, round)).toBe("conflict");
  });

  it("功能字段上的提案也算落在这章（s5/feat.acceptance → s5）", () => {
    const round = {
      draft: { proposals: [{ proposal_id: "p1", target_label: "s5/订单导出.acceptance" }] },
    } as never;
    const s5 = { section_id: "s5", title: "功能需求", blocks: [] };
    expect(sectionStatus(s5, round)).toBe("gap");
  });

  it("偏好区不落到任何一章", () => {
    const round = {
      draft: { proposals: [{ proposal_id: "p1", target_label: "preference" }] },
    } as never;
    expect(sectionStatus(empty, round)).toBe("empty");
  });

  it("有内容但本轮还有提案 → gap 盖过 filled（要你再确认）", () => {
    const round = {
      draft: { proposals: [{ proposal_id: "p1", target_label: "s1" }] },
    } as never;
    expect(sectionStatus(filled, round)).toBe("gap");
  });
});

describe("sectionIdOf", () => {
  it.each([
    ["s5", "s5"],
    ["s5/订单导出", "s5"],
    ["s5/订单导出.acceptance", "s5"],
    ["preference", "preference"],
  ])("%s → %s", (target, expected) => {
    expect(sectionIdOf(target)).toBe(expected);
  });
});

describe("sectionBadge", () => {
  const item = (section_id: string, severity: "blocker" | "warn"): ReadinessItemView => ({
    section_id,
    code: "section_min_entries",
    title: "背景与问题还缺 1 条内容",
    detail: "至少 1 条",
    severity,
  });

  it("该章 2 阻塞 1 提示时戳分开计数", () => {
    expect(
      sectionBadge("s5", [item("s5", "blocker"), item("s5", "blocker"), item("s5", "warn"), item("s9", "blocker")]),
    ).toEqual({ block: 2, warn: 1 });
  });

  it("无缺口返回 null（不渲染戳）", () => {
    expect(sectionBadge("s1", [])).toBeNull();
  });
});

describe("filledSectionCount", () => {
  it("空文档是 0 章，只有空壳的块不算内容", () => {
    expect(filledSectionCount({ sections: [empty, { ...empty, section_id: "s10" }] })).toBe(0);
  });

  it("有块的章才计数", () => {
    expect(filledSectionCount({ sections: [filled, empty] })).toBe(1);
  });

  it("没有文档就是 0", () => {
    expect(filledSectionCount(null)).toBe(0);
  });
});
