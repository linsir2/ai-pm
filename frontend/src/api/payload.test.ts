import { describe, expect, it } from "vitest";

import {
  allProposalIds,
  awaitingMode,
  buildAnswers,
  buildClaimEdits,
  buildOpeningBody,
  defaultAccepted,
  defaultSeats,
  initialClaimEdits,
  integrationSummary,
  selectableLabels,
  seatNominationSummary,
  unansweredCount,
} from "./payload";
import type { RoundView } from "./types";

const round = {
  round_id: "r1",
  step: "awaiting_answers",
  waiting_on: "questionnaire",
  user_input: "想做个订单导出的功能",
  seat_round_count: 1,
  seats: ["seat.data_scale"],
  nominated_seats: [
    { seat_id: "seat.data_scale", title: "数据规模", focus: "数据量决定方案" },
    { seat_id: "seat.ux_flow", title: "交互流程", focus: "导出怎么点" },
  ],
  form_id: "f1",
  list_items: ["先记一条"],
  claims: [
    { claim_id: "c1", label: "need", target_label: "s5" },
    { claim_id: "c2", label: "guess", target_label: "s10" },
    { claim_id: "c3", label: "preference", target_label: "preference" },
    { claim_id: "c4", label: "decision", target_label: "s5/订单导出.priority" },
  ],
  questions: [
    { question_id: "q1", kind: "gap" },
    { question_id: "q2", kind: "gap" },
    { question_id: "q3", kind: "gap" },
  ],
  draft: {
    proposals: [
      { proposal_id: "p01", origin_type: "user" },
      { proposal_id: "p02", origin_type: "derived" },
      { proposal_id: "p03", origin_type: "proposed" },
      { proposal_id: "p04", origin_type: "system_default" },
    ],
  },
} as unknown as RoundView;

describe("buildAnswers", () => {
  it("答了的进 answers，没答的进 skipped（跳过后果由题面写清）", () => {
    const body = buildAnswers(round, {
      q1: { selected: ["b"], text: "差不多这个量级" },
      q3: { selected: [], text: "", skipped: true },
    });
    expect(body.form_id).toBe("f1");
    expect(body.answers).toEqual([
      { question_id: "q1", selected: ["b"], text: "差不多这个量级" },
    ]);
    expect(body.skipped.sort()).toEqual(["q2", "q3"]);
  });

  it("交了空卷也要有表单项（至少一条答案或跳过）", () => {
    const body = buildAnswers(round, {});
    expect(body.answers).toEqual([]);
    expect(body.skipped).toEqual(["q1", "q2", "q3"]);
  });

  it("只有空白文本不算答过", () => {
    const body = buildAnswers(round, { q1: { selected: [], text: "   " } });
    expect(body.answers).toEqual([]);
    expect(body.skipped).toContain("q1");
  });
});

describe("unansweredCount", () => {
  it("按钮上那句「未答的 N 题将记入开放问题」用的就是它", () => {
    expect(unansweredCount(round, {})).toBe(3);
    expect(unansweredCount(round, { q1: { selected: ["a"], text: "" } })).toBe(2);
  });
});

describe("defaultAccepted", () => {
  it("提议组默认不勾，其余默认勾上", () => {
    expect(defaultAccepted(round.draft!)).toEqual(["p01", "p02", "p04"]);
  });
});

describe("allProposalIds", () => {
  it("本批全接受：三组全覆盖，包含默认不勾的那一组", () => {
    expect(allProposalIds(round.draft!)).toEqual(["p01", "p02", "p03", "p04"]);
  });
});

describe("buildClaimEdits", () => {
  it("只提交改过的条目", () => {
    const edits = buildClaimEdits(round, { c2: { label: "fact", target_label: "s3" } });
    expect(edits).toEqual([
      { claim_id: "c2", label: "fact", target: { zone: "body", section_id: "s3" } },
    ]);
  });

  it("标签改回原样就算没改，不提交", () => {
    expect(buildClaimEdits(round, { c1: { label: "need", target_label: "s5" } })).toEqual([]);
  });

  it("改成偏好时去处必须是偏好区（后端会因标签与分区不符直接拒）", () => {
    const edits = buildClaimEdits(round, { c1: { label: "preference", target_label: "s5" } });
    expect(edits).toEqual([
      { claim_id: "c1", label: "preference", target: { zone: "preference" } },
    ]);
  });

  it("功能字段上的主张按章节比，不因为带了 feature 就当成改过", () => {
    const edits = buildClaimEdits(round, {
      c4: { label: "decision", target_label: "s5" },
    });
    expect(edits).toEqual([]);
  });
});

describe("initialClaimEdits", () => {
  it("初值就是后端给的现状，去处显示到章节一级", () => {
    expect(initialClaimEdits(round)).toEqual({
      c1: { label: "need", target_label: "s5" },
      c2: { label: "guess", target_label: "s10" },
      c3: { label: "preference", target_label: "preference" },
      c4: { label: "decision", target_label: "s5" },
    });
  });
});

describe("selectableLabels", () => {
  it("猜测带了假设，这一版改不了标签", () => {
    expect(selectableLabels("guess")).toEqual(["guess"]);
  });

  it("决策可以退回去，但改不回决策（拍板人补不了）", () => {
    expect(selectableLabels("decision")).toEqual([
      "decision",
      "fact",
      "need",
      "goal",
      "constraint",
      "preference",
    ]);
    expect(selectableLabels("fact")).not.toContain("decision");
  });

  it("普通标签都能改，除了要补字段的那两个", () => {
    expect(selectableLabels("need")).toEqual([
      "fact",
      "need",
      "goal",
      "constraint",
      "preference",
    ]);
  });
});

describe("buildOpeningBody", () => {
  it("席位 + 只改过的纠正", () => {
    const body = buildOpeningBody(round, ["seat.data_scale"], {
      c2: { label: "constraint", target_label: "s10" },
    });
    expect(body).toEqual({
      seats: ["seat.data_scale"],
      claim_edits: [
        { claim_id: "c2", label: "constraint", target: { zone: "body", section_id: "s10" } },
      ],
    });
  });
});

describe("defaultSeats", () => {
  it("提名的席位默认全选（可删）", () => {
    expect(defaultSeats(round)).toEqual(["seat.data_scale", "seat.ux_flow"]);
  });
});

describe("seatNominationSummary", () => {
  it("名单来自分析者时如实说 agent，并给出还能加几个", () => {
    const view = {
      nominated_seats: [
        { seat_id: "seat.export_import", why: "这轮就是在做导出", source: "agent" },
      ],
      available_seats: [{ seat_id: "seat.data_scale" }, { seat_id: "seat.permissions" }],
    } as unknown as RoundView;
    expect(seatNominationSummary(view)).toEqual({
      nominated: 1,
      available: 2,
      source: "agent",
    });
  });

  it("老轮次回落目录匹配时标 catalog", () => {
    const view = {
      nominated_seats: [{ seat_id: "seat.data_scale", why: "", source: "catalog" }],
      available_seats: [],
    } as unknown as RoundView;
    expect(seatNominationSummary(view).source).toBe("catalog");
  });

  it("一个都没有时是 empty", () => {
    const view = { nominated_seats: [], available_seats: [] } as unknown as RoundView;
    expect(seatNominationSummary(view).source).toBe("empty");
  });
});

describe("awaitingMode", () => {
  const base = { ...round } as unknown as RoundView;

  it("没有轮次 → 输入框", () => {
    expect(awaitingMode(null)).toBe("input");
  });

  it("等复述 → 复述卡（常驻在闭环区，不弹抽屉）", () => {
    expect(awaitingMode({ ...base, waiting_on: "opening_card" } as RoundView)).toBe("opening");
  });

  it("有题目才弹问卷；还没取回题目就算在等数据", () => {
    expect(awaitingMode(base)).toBe("questionnaire");
    expect(
      awaitingMode({ ...base, questions: [] } as unknown as RoundView),
    ).toBe("loading");
  });

  it("有草案才弹草案卡", () => {
    expect(
      awaitingMode({ ...base, waiting_on: "draft_card", draft: { proposals: [] } } as unknown as RoundView),
    ).toBe("draft");
    expect(
      awaitingMode({ ...base, waiting_on: "draft_card", draft: null } as unknown as RoundView),
    ).toBe("loading");
  });

  it("席位在跑 → 等数据（不显示输入框，也不弹卡）", () => {
    expect(
      awaitingMode({ ...base, waiting_on: "none", step: "seats_running" } as RoundView),
    ).toBe("loading");
  });

  it("结束、失败与放弃各有各的样子", () => {
    expect(awaitingMode({ ...base, step: "done" } as RoundView)).toBe("done");
    expect(awaitingMode({ ...base, step: "failed" } as RoundView)).toBe("failed");
    // 放弃是终点，不是"还在跑"——否则输入框会被永远锁住
    expect(
      awaitingMode({
        ...base,
        step: "abandoned",
        waiting_on: "none",
      } as unknown as RoundView),
    ).toBe("abandoned");
  });
});

describe("integrationSummary", () => {
  it("整合做了什么，一行说清", () => {
    expect(integrationSummary(round)).toBe("整合：出了 3 题 · 出了 4 条候选 · 1 条先记成清单");
  });

  it("什么都没出也说得出话", () => {
    expect(
      integrationSummary({ ...round, questions: [], draft: null, list_items: [] } as unknown as RoundView),
    ).toBe("整合完成");
  });
});
