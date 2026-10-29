/* 研究页签的纯函数（P3b-1）：只测判据，不测渲染。 */

import { describe, expect, it } from "vitest";

import type { ExplorationView, RoundView } from "../api/types";
import {
  buildResearchBody,
  canStartResearch,
  hasFindings,
  inquiryRuns,
  researchRuns,
  researchStatusText,
  researchTopicError,
  sourceAction,
  sourceActionReason,
} from "./research";

function run(over: Partial<ExplorationView> = {}): ExplorationView {
  return {
    exploration_id: "x-1",
    project_id: "p1",
    kind: "web_research",
    status: "done",
    scope_kind: "topic",
    scope_ref: null,
    scope_text: "导出上限",
    created_at: "2026-10-29T00:00:00Z",
    failure_reason: null,
    abandon_reason: null,
    overflow_count: 0,
    candidates: [],
    research: {
      findings: [{ finding_id: "F1", statement: "60 秒上限", refs: ["E1"] }],
      sources: [
        {
          eid: "E1",
          title: "白皮书",
          url: "https://example.com/a",
          excerpt: "同类产品默认 60 秒上限。",
        },
      ],
      open_questions: [],
      notes: [],
    },
    ...over,
  };
}

function round_(over: Partial<RoundView> = {}): RoundView {
  return { round_id: "r-1", step: "seats_running", research_sources: [], ...over } as RoundView;
}

describe("research 页签的判据", () => {
  it("研究行与调研行互不混", () => {
    const inquiry = run({ kind: "inquiry", research: null });
    const list = [run(), inquiry];

    expect(researchRuns(list).map((item) => item.kind)).toEqual(["web_research"]);
    expect(inquiryRuns(list).map((item) => item.kind)).toEqual(["inquiry"]);
  });

  it("同项目在跑时不能开第二趟", () => {
    const running = run({ status: "running" });

    expect(canStartResearch([running], false)).toBe(false);
    expect(canStartResearch([], false)).toBe(true);
    expect(canStartResearch([], true)).toBe(false);
  });

  it("主题太短/太长都会被拦（上限来自后端）", () => {
    expect(researchTopicError("   ", 500)).toBe("写清要研究什么");
    expect(researchTopicError("导出上限", 500)).toBeNull();
    expect(researchTopicError("字".repeat(6), 5)).toBe("最多 5 字");
    expect(researchTopicError("字".repeat(6))).toBeNull(); // 拿不到上限就不拦
  });

  it("发给后端的正文只带主题，并去掉首尾空白", () => {
    expect(buildResearchBody("  导出上限  ")).toEqual({ topic: "导出上限" });
  });

  it("状态那句话：失败带原因，完成带来源数", () => {
    expect(researchStatusText(run({ status: "running" }))).toBe("研究进行中…");
    expect(researchStatusText(run())).toBe("完成：1 条来源");
    expect(researchStatusText(run({ status: "failed", failure_reason: "没来源" }))).toBe(
      "失败：没来源",
    );
    expect(
      researchStatusText(run({ status: "failed", failure_reason: null })),
    ).toContain("没留下原因");
    expect(researchStatusText(run({ status: "abandoned", abandon_reason: "不查了" }))).toBe(
      "已放弃：不查了",
    );
  });

  it("来源卡按钮：能挂 / 已挂 / 不能挂（且有原因）", () => {
    const source = run().research!.sources[0]!;

    expect(sourceAction(source, null)).toBe("blocked");
    expect(sourceActionReason("blocked", null)).toBe("先开一轮，再挂来源");

    expect(sourceAction(source, round_())).toBe("attach");
    expect(sourceActionReason("attach", round_())).toBeNull();

    expect(
      sourceAction(source, round_({ research_sources: [{ eid: "E1" } as never] })),
    ).toBe("attached");

    const drafted = round_({ step: "awaiting_confirm" });
    expect(sourceAction(source, drafted)).toBe("blocked");
    expect(sourceActionReason("blocked", drafted)).toContain("出了草案");
  });

  it("空结论要能说出来（只找到来源不算失败）", () => {
    expect(hasFindings(run())).toBe(true);
    expect(hasFindings(run({ research: { ...run().research!, findings: [] } }))).toBe(false);
  });
});
