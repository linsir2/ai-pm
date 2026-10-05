import { describe, expect, it } from "vitest";

import {
  allProposalIds,
  answerBatchSummary,
  appendSeedText,
  awaitingMode,
  buildAnswers,
  buildClaimEdits,
  buildExplorationBody,
  buildOpeningBody,
  buildStart,
  defaultAccepted,
  defaultSeats,
  groupAdvisories,
  initialClaimEdits,
  integrationSummary,
  materialHitTarget,
  selectableLabels,
  seatNominationSummary,
  summarizeEids,
  targetBody,
  targetOptions,
  unansweredCount,
} from "./payload";
import type {
  DocumentView,
  MaterialSummaryView,
  RoundMaterialView,
  RoundView,
} from "./types";

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

  it("从功能字段改回整章也算改过（F7：去处比的是完整 label，不再只看章节）", () => {
    const edits = buildClaimEdits(round, {
      c4: { label: "decision", target_label: "s5" },
    });
    expect(edits).toEqual([
      { claim_id: "c4", label: "decision", target: { zone: "body", section_id: "s5" } },
    ]);
  });

  it("落到某个功能的某个字段：请求体给全三件套（F7）", () => {
    const edits = buildClaimEdits(round, {
      c4: { label: "decision", target_label: "s5/order-export.acceptance" },
    });
    expect(edits).toEqual([
      {
        claim_id: "c4",
        label: "decision",
        target: {
          zone: "body",
          section_id: "s5",
          feature_slug: "order-export",
          field: "acceptance",
        },
      },
    ]);
  });
});

describe("initialClaimEdits", () => {
  it("初值就是后端给的现状，去处原样显示（含功能字段）", () => {
    expect(initialClaimEdits(round)).toEqual({
      c1: { label: "need", target_label: "s5" },
      c2: { label: "guess", target_label: "s10" },
      c3: { label: "preference", target_label: "preference" },
      c4: { label: "decision", target_label: "s5/订单导出.priority" },
    });
  });
});

describe("targetBody", () => {
  it("四种去处形状各自变对（与后端 Target.label 互为逆运算）", () => {
    expect(targetBody("preference")).toEqual({ zone: "preference" });
    expect(targetBody("s5")).toEqual({ zone: "body", section_id: "s5" });
    expect(targetBody("s5/order-export")).toEqual({
      zone: "body",
      section_id: "s5",
      feature_slug: "order-export",
    });
    expect(targetBody("s5/order-export.acceptance")).toEqual({
      zone: "body",
      section_id: "s5",
      feature_slug: "order-export",
      field: "acceptance",
    });
    // 章节槽（s8 的六块）：没有斜杠，点是分隔符
    expect(targetBody("s8.performance")).toEqual({
      zone: "body",
      section_id: "s8",
      field: "performance",
    });
  });
});

const doc = {
  sections: [
    {
      section_id: "s5",
      title: "功能需求",
      blocks: [
        {
          block_id: "feat-order-export",
          kind: "feature",
          feature_slug: "order-export",
          field: null,
          entries: [],
          children: [
            {
              block_id: "feat-order-export.name",
              kind: "text",
              field: "name",
              field_title: "名称",
              entries: [{ entry_id: "e1", text: "订单导出", refs: [] }],
              children: [],
            },
          ],
        },
      ],
    },
    { section_id: "s8", title: "非功能需求", blocks: [] },
  ],
} as unknown as DocumentView;

describe("targetOptions", () => {
  it("章节与「功能 · 字段」都在选项里；字段块还不存在也能选（F7）", () => {
    const options = targetOptions(doc);
    const values = options.map((option) => option.value);

    expect(values).toContain("preference");
    expect(values).toContain("s8");
    expect(values).toContain("s5/order-export.name");
    // 验收标准这块还不存在，但它是功能字段表里的一员 → 也要能选
    expect(values).toContain("s5/order-export.acceptance");
    expect(options.find((option) => option.value === "s5/order-export.acceptance")!.text).toBe(
      "订单导出 · 验收标准",
    );
  });

  it("没有文档就只剩偏好区与 13 章", () => {
    const values = targetOptions(null).map((option) => option.value);
    expect(values).toEqual(["preference"]);
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
      // 契约变更（工具层 C-2b）：起轮体多带一把 `web_queries`，不勾就是空数组
      web_queries: [],
    });
  });

  it("把勾中的检索词一起递出去（没勾就是空数组）", () => {
    const body = buildOpeningBody(round, ["seat.data_scale"], {}, ["RBAC 权限模型"]);

    expect(body.web_queries).toEqual(["RBAC 权限模型"]);
    expect(buildOpeningBody(round, ["seat.data_scale"], {}).web_queries).toEqual([]);
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

describe("answerBatchSummary", () => {
  it("答了几条、跳了几条，一行说清", () => {
    expect(answerBatchSummary({ form_id: "f1", answered: 2, skipped: 1, answers: [] })).toBe(
      "已答 2 · 跳过 1",
    );
    expect(answerBatchSummary({ form_id: "f2", answered: 3, skipped: 0, answers: [] })).toBe(
      "已答 3",
    );
  });
});

describe("buildStart", () => {
  it("没选材料、没带种子时都是空数组（不是 undefined）", () => {
    expect(buildStart("想做个导出", [])).toEqual({
      input: "想做个导出",
      material_ids: [],
      seed_ids: [],
    });
  });

  it("选了材料就按顺序带上 id", () => {
    const materials = [
      { material_id: "m1", title: "需求.md" },
      { material_id: "m2", title: "spec.pdf" },
    ] as unknown as MaterialSummaryView[];

    expect(buildStart("按这两份写", materials)).toEqual({
      input: "按这两份写",
      material_ids: ["m1", "m2"],
      seed_ids: [],
    });
  });

  it("收下的调研产物按顺序带上种子 id（M8：`探索id#候选id`）", () => {
    expect(buildStart("接着调研的结论写", [], ["x-1#H1", "x-1#B2"])).toEqual({
      input: "接着调研的结论写",
      material_ids: [],
      seed_ids: ["x-1#H1", "x-1#B2"],
    });
  });
});

describe("appendSeedText", () => {
  it("输入框空着时直接填入（不加前导换行）", () => {
    expect(appendSeedText("", "导出要支持筛选")).toBe("导出要支持筛选");
  });

  it("已有内容时换行追加，不覆盖用户打的字", () => {
    expect(appendSeedText("先做个订单导出", "需要搞清楚：权限边界")).toBe(
      "先做个订单导出\n需要搞清楚：权限边界",
    );
  });

  it("空文本不加分隔（也不会多出一个空行）", () => {
    expect(appendSeedText("已有内容", "")).toBe("已有内容");
    expect(appendSeedText("", "")).toBe("");
  });

  it("连收三条，一条一行", () => {
    let text = "";
    text = appendSeedText(text, "一");
    text = appendSeedText(text, "二");
    text = appendSeedText(text, "三");
    expect(text).toBe("一\n二\n三");
  });
});

describe("buildExplorationBody", () => {
  it("条目锚点带上条目 id 与原文", () => {
    expect(buildExplorationBody({ kind: "entry", ref: "e1", focus: "导出要有筛选" })).toEqual({
      kind: "inquiry",
      scope: { kind: "entry", ref: "e1", text: "导出要有筛选" },
    });
  });

  it("整份文档锚点不带 ref/text（后端按整份文档调研）", () => {
    expect(buildExplorationBody({ kind: "document" })).toEqual({
      kind: "inquiry",
      scope: { kind: "document", ref: null, text: null },
    });
  });
});

describe("materialHitTarget", () => {
  const materials = [
    {
      material_id: "m1",
      title: "诊断报告.pdf",
      chunks: [{ chunk_id: "m1#c03", eid: "E7", anchor: "第 3 页", text: "…" }],
    },
  ] as unknown as RoundMaterialView[];

  it("命中块在本轮材料里 → 能跳，并告诉你跳去哪份材料的哪一块", () => {
    expect(materialHitTarget("m1#c03", materials)).toEqual({
      found: true,
      title: "诊断报告.pdf",
      anchor: "第 3 页",
    });
  });

  it("命中块不在本轮材料里 → 说实话（还没进过任何一轮，所以没有编号）", () => {
    expect(materialHitTarget("m9#c00", materials)).toEqual({ found: false });
    expect(materialHitTarget("m1#c03", [])).toEqual({ found: false });
  });
});
describe("groupAdvisories（P29 眼睛检查 F3）", () => {
  it("相同的告警合成一条并带上条数（草案卡上三句一模一样的话，用户分不清是哪三条）", () => {
    const grouped = groupAdvisories([
      "s12：这条落在 s12，不在本轮去处里",
      "s3：这条落在 s3，不在本轮去处里",
      "s12：这条落在 s12，不在本轮去处里",
      "s12：这条落在 s12，不在本轮去处里",
    ]);

    expect(grouped).toEqual([
      { text: "s12：这条落在 s12，不在本轮去处里", count: 3 },
      { text: "s3：这条落在 s3，不在本轮去处里", count: 1 },
    ]);
  });

  it("顺序按**第一次出现**（先说的先说，不排序）", () => {
    const grouped = groupAdvisories(["乙", "甲", "乙"]);

    expect(grouped.map((item) => item.text)).toEqual(["乙", "甲"]);
  });

  it("空列表回空", () => {
    expect(groupAdvisories([])).toEqual([]);
  });
});

describe("summarizeEids（P29 眼睛检查 F5）", () => {
  it("只显示前几个，其余报数——材料库那三十多个编号不再竖排铺开", () => {
    const summary = summarizeEids(["E18", "E19", "E20", "E21", "E22"], 3);

    expect(summary.shown).toEqual(["E18", "E19", "E20"]);
    expect(summary.rest).toBe(2);
    expect(summary.title).toBe("E18、E19、E20、E21、E22");   // 全量进 title，看得见
  });

  it("没超出就一个都不省", () => {
    const summary = summarizeEids(["E1", "E2"], 3);

    expect(summary.shown).toEqual(["E1", "E2"]);
    expect(summary.rest).toBe(0);
  });
});
