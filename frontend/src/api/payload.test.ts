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
  discussionSeedId,
  explorationSeedId,
  groupAdvisories,
  initialClaimEdits,
  integrationSummary,
  materialHitNotice,
  materialHitTarget,
  openingApprovedRequestIds,
  openingWebRequestChoice,
  openingWebRequests,
  searchHitAction,
  selectableLabels,
  seatNominationSummary,
  summarizeEids,
  targetBody,
  targetOptions,
  unansweredCount,
  webOriginText,
  webRequestAction,
  webRequestStateText,
} from "./payload";
import type {
  DocumentView,
  MaterialSummaryView,
  RoundMaterialView,
  RoundView,
  SearchHitView,
  WebRequestView,
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

// 2026-10-29「整块重述式 add」防线：装配层给每条 add 提案带行级逐字重叠快照
// `overlap`（"dup/total"）。有值的默认不勾（看清再勾）；null/无键（老轮次）照旧保留。
describe("defaultAccepted 的 overlap 防线", () => {
  const withOverlapMark = {
    draft: {
      proposals: [
        { proposal_id: "p10", origin_type: "user", overlap: "3/4" },
        { proposal_id: "p11", origin_type: "derived", overlap: null },
        // 老轮次的载荷压根没有 overlap 这个键（可选字段先例 simulation_based）
        { proposal_id: "p12", origin_type: "system_default" },
        { proposal_id: "p13", origin_type: "proposed", overlap: "1/4" },
      ],
    },
  } as unknown as RoundView;

  it('overlap 有值（"3/4"）的提案不进默认勾选——想勾得看清楚再自己点', () => {
    expect(defaultAccepted(withOverlapMark.draft!)).toEqual(["p11", "p12"]);
  });

  it("overlap 为 null 或没有键的派生提案照旧默认勾上（老轮次不回退）", () => {
    const accepted = defaultAccepted(withOverlapMark.draft!);
    expect(accepted).toContain("p11");
    expect(accepted).toContain("p12");
  });

  it("proposed 依旧默认不勾——overlap 规则不吃掉这条既有锚", () => {
    expect(defaultAccepted(withOverlapMark.draft!)).not.toContain("p13");
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
      // P3a：旧字段保留，新字段默认“不自动批准、没有逐条批准”
      approved_request_ids: [],
      allow_web: false,
    });
  });

  it("把勾中的检索词一起递出去（没勾就是空数组）", () => {
    const body = buildOpeningBody(round, ["seat.data_scale"], {}, ["RBAC 权限模型"]);

    expect(body.web_queries).toEqual(["RBAC 权限模型"]);
    expect(buildOpeningBody(round, ["seat.data_scale"], {}).web_queries).toEqual([]);
  });

  it("逐条批准的申请 id 与 allow_web 随 body 一起走（旧字段同时保留）", () => {
    const view = {
      ...round,
      web_requests: [
        { request_id: "w1", state: "proposed" },
        { request_id: "w2", state: "approved" },
      ],
    } as unknown as RoundView;
    const body = buildOpeningBody(
      view,
      ["seat.data_scale"],
      {},
      ["RBAC 权限模型"],
      ["w1", "w2"],
      true,
    );

    expect(body.approved_request_ids).toEqual(["w1", "w2"]);
    expect(body.allow_web).toBe(true);
    expect(body.web_queries).toEqual(["RBAC 权限模型"]);
  });

  it("body 只发当前仍可批的 proposed/approved id（R24）", () => {
    const view = {
      ...round,
      web_requests: [
        { request_id: "w1", state: "proposed" },
        { request_id: "w2", state: "approved" },
        { request_id: "w3", state: "rejected" },
        { request_id: "w4", state: "done" },
        { request_id: "w5", state: "failed" },
      ],
    } as unknown as RoundView;
    const body = buildOpeningBody(
      view,
      ["seat.data_scale"],
      {},
      [],
      ["w1", "w2", "w3", "w4", "w5"],
    );

    expect(body.approved_request_ids).toEqual(["w1", "w2"]);
  });

  it("勾选后同轮刷新变 rejected：body 里不再带那个 id（R24）", () => {
    const afterRefresh = {
      ...round,
      web_requests: [
        { request_id: "w1", state: "rejected" },
        { request_id: "w2", state: "proposed" },
      ],
    } as unknown as RoundView;
    const body = buildOpeningBody(
      afterRefresh,
      ["seat.data_scale"],
      {},
      [],
      ["w1", "w2"],
    );

    expect(body.approved_request_ids).toEqual(["w2"]);
  });
});

describe("openingWebRequests（新结构优先，旧检索词不重复渲染）", () => {
  function request(over: Partial<WebRequestView> = {}): WebRequestView {
    return {
      request_id: "w1",
      query: "RBAC 权限模型",
      why: "要查行业通行做法",
      origin: "analyzer",
      seat_id: null,
      pass_index: null,
      state: "proposed",
      result_note: null,
      created_at: null,
      ...over,
    };
  }

  it("有 analyzer 申请时只用新卡，旧 web_queries 回退为空", () => {
    const view = {
      ...round,
      web_queries: [{ query: "旧检索词", why: "旧理由", chosen: false }],
      web_requests: [request()],
    } as unknown as RoundView;

    const opening = openingWebRequests(view);
    expect(opening.requests.map((item) => item.request_id)).toEqual(["w1"]);
    expect(opening.legacy).toEqual([]);
  });

  it("没有 analyzer 申请时回退旧 web_queries", () => {
    const view = {
      ...round,
      web_queries: [{ query: "旧检索词", why: "旧理由", chosen: false }],
      web_requests: [request({ origin: "seat" })],
    } as unknown as RoundView;

    const opening = openingWebRequests(view);
    expect(opening.requests).toEqual([]);
    expect(opening.legacy.map((item) => item.query)).toEqual(["旧检索词"]);
  });
});

describe("openingApprovedRequestIds（开场卡只发仍可批的 id）", () => {
  it("按当前 round 过滤：rejected/done/failed 不发，proposed/approved 保留", () => {
    const view = {
      ...round,
      web_requests: [
        { request_id: "w1", state: "proposed" },
        { request_id: "w2", state: "approved" },
        { request_id: "w3", state: "rejected" },
        { request_id: "w4", state: "done" },
        { request_id: "w5", state: "failed" },
      ],
    } as unknown as RoundView;

    expect(openingApprovedRequestIds(view, ["w1", "w2", "w3", "w4", "w5"])).toEqual([
      "w1",
      "w2",
    ]);
    expect(openingApprovedRequestIds(view, ["w3", "w4", "w5"])).toEqual([]);
    expect(openingApprovedRequestIds(view, ["missing"])).toEqual([]);
  });
});

describe("openingWebRequestChoice（开场卡名额）", () => {
  function request(over: Partial<WebRequestView> = {}): WebRequestView {
    return {
      request_id: "w1",
      query: "RBAC 权限模型",
      why: "要查行业通行做法",
      origin: "analyzer",
      seat_id: null,
      pass_index: null,
      state: "proposed",
      result_note: null,
      created_at: null,
      ...over,
    };
  }

  const ready = {
    used: 0,
    selected: 0,
    limit: 2,
    available: true,
    pending: false,
  };

  it("limit=2 已勾 2 条：第 3 条未勾项禁用并说明剩余 0", () => {
    const choice = openingWebRequestChoice(request({ request_id: "w3" }), false, {
      ...ready,
      selected: 2,
    });

    expect(choice.disabled).toBe(true);
    expect(choice.reason).toContain("名额已用完");
  });

  it("已勾的条目仍可取消（名额满不把已勾项锁死）", () => {
    const choice = openingWebRequestChoice(request(), true, { ...ready, selected: 2 });
    expect(choice.disabled).toBe(false);
  });

  it("limit 没读到：未勾禁用并说明；已勾仍能取消", () => {
    const unchecked = openingWebRequestChoice(request(), false, { ...ready, limit: null });
    expect(unchecked.disabled).toBe(true);
    expect(unchecked.reason).toContain("还没读到");

    const checked = openingWebRequestChoice(request(), true, { ...ready, limit: null });
    expect(checked.disabled).toBe(false);
  });

  it("没有联网工具：禁用并说明", () => {
    const choice = openingWebRequestChoice(request(), false, {
      ...ready,
      available: false,
    });
    expect(choice.disabled).toBe(true);
    expect(choice.reason).toContain("没配联网工具");
  });

  it("提交中：禁用并说明", () => {
    const choice = openingWebRequestChoice(request(), false, { ...ready, pending: true });
    expect(choice.disabled).toBe(true);
    expect(choice.reason).toContain("正在提交");
  });

  it("服务端已批准：勾着且只读；rejected 等终态：未勾且只读", () => {
    const approved = openingWebRequestChoice(request({ state: "approved" }), false, ready);
    expect(approved.checked).toBe(true);
    expect(approved.disabled).toBe(true);
    expect(approved.stateLabel).toBe("已批准，等待执行");

    const rejected = openingWebRequestChoice(request({ state: "rejected" }), false, ready);
    expect(rejected.checked).toBe(false);
    expect(rejected.disabled).toBe(true);
    expect(rejected.stateLabel).toBe("已拒绝");
  });

  it("还有名额：未勾项可勾（limit=3、已用 1 时还剩 2）", () => {
    const choice = openingWebRequestChoice(request(), false, {
      ...ready,
      used: 1,
      limit: 3,
    });
    expect(choice.disabled).toBe(false);
    expect(choice.reason).toBeNull();
  });
});

describe("webRequestAction（能批不能批只在纯函数里判）", () => {
  const ready = {
    used: 0,
    limit: 3,
    available: true,
    busy: false,
    pending: false,
    historical: false,
    terminal: false,
  };

  function request(over: Partial<WebRequestView> = {}): WebRequestView {
    return {
      request_id: "w1",
      query: "RBAC 权限模型",
      why: "要查行业通行做法",
      origin: "seat",
      seat_id: "seat.permissions",
      pass_index: 1,
      state: "proposed",
      result_note: null,
      created_at: null,
      ...over,
    };
  }

  it("proposed：批准与拒绝都能点", () => {
    const action = webRequestAction(request(), ready);
    expect(action.visible).toBe(true);
    expect(action.approve).toEqual({ enabled: true, reason: null });
    expect(action.reject).toEqual({ enabled: true, reason: null });
  });

  it("名额用完：批准禁用并说明，拒绝仍可点", () => {
    const action = webRequestAction(request(), { ...ready, used: 3 });
    expect(action.visible).toBe(true);
    expect(action.approve.enabled).toBe(false);
    expect(action.approve.reason).toContain("名额已用完");
    expect(action.reject.enabled).toBe(true);
  });

  it("还没读到名额：批准禁用，不硬编码 3", () => {
    const action = webRequestAction(request(), { ...ready, limit: null });
    expect(action.approve.enabled).toBe(false);
    expect(action.approve.reason).toContain("还没读到");
  });

  it("服务端没有联网工具：批准禁用并说明，拒绝仍可点", () => {
    const action = webRequestAction(request(), { ...ready, available: false });
    expect(action.approve.enabled).toBe(false);
    expect(action.approve.reason).toContain("没配联网工具");
    expect(action.reject.enabled).toBe(true);
  });

  it("轮忙：批准与拒绝都禁用并说明", () => {
    const action = webRequestAction(request(), { ...ready, busy: true });
    expect(action.approve.enabled).toBe(false);
    expect(action.reject.enabled).toBe(false);
    expect(action.approve.reason).toContain("还有动作在跑");
  });

  it("这一条在处理：按钮禁用并说明", () => {
    const action = webRequestAction(request(), { ...ready, pending: true });
    expect(action.approve.enabled).toBe(false);
    expect(action.reject.enabled).toBe(false);
    expect(action.approve.reason).toContain("正在处理");
  });

  it("历史轮只读：决策禁用并说明", () => {
    const action = webRequestAction(request(), { ...ready, historical: true });
    expect(action.approve.enabled).toBe(false);
    expect(action.reject.enabled).toBe(false);
    expect(action.approve.reason).toContain("历史轮次只读");
  });

  it("轮已结束：决策禁用并说明", () => {
    const action = webRequestAction(request(), { ...ready, terminal: true });
    expect(action.approve.enabled).toBe(false);
    expect(action.reject.enabled).toBe(false);
    expect(action.approve.reason).toContain("已经结束");
  });

  it.each(["approved", "done", "failed", "rejected"])(
    "非 proposed（%s）不显示决策按钮",
    (state) => {
      expect(webRequestAction(request({ state }), ready).visible).toBe(false);
    },
  );

  it("超额 approved：只出逐条恢复，执行与放弃都可点", () => {
    const action = webRequestAction(request({ state: "approved" }), {
      ...ready,
      used: 4,
    });
    expect(action.recovery.visible).toBe(true);
    expect(action.recovery.execute).toEqual({ enabled: true, reason: null });
    expect(action.recovery.abandon).toEqual({ enabled: true, reason: null });
  });

  it("approved 但没超额：不出恢复（它还能被自动执行）", () => {
    const action = webRequestAction(request({ state: "approved" }), {
      ...ready,
      used: 3,
    });
    expect(action.recovery.visible).toBe(false);
  });

  it("没配联网工具：恢复的执行禁用、放弃仍可点", () => {
    const action = webRequestAction(request({ state: "approved" }), {
      ...ready,
      used: 4,
      available: false,
    });
    expect(action.recovery.execute.enabled).toBe(false);
    expect(action.recovery.execute.reason).toContain("只能放弃");
    expect(action.recovery.abandon.enabled).toBe(true);
  });

  it.each([
    ["busy" as const, "还有动作在跑"],
    ["historical" as const, "历史轮次只读"],
    ["terminal" as const, "已经结束"],
    ["pending" as const, "正在处理"],
  ])("%s：恢复两个动作都禁用并说明", (flag, text) => {
    const action = webRequestAction(request({ state: "approved" }), {
      ...ready,
      used: 4,
      [flag]: true,
    });
    expect(action.recovery.visible).toBe(true);
    expect(action.recovery.execute.enabled).toBe(false);
    expect(action.recovery.abandon.enabled).toBe(false);
    expect(action.recovery.execute.reason).toContain(text);
  });
});

describe("联网申请的展示词", () => {
  it("来源与状态说人话，认不出就照实显示原值", () => {
    expect(webOriginText("analyzer")).toBe("分析席位");
    expect(webOriginText("seat")).toBe("席位");
    expect(webOriginText("integrator")).toBe("整合者");
    expect(webOriginText("robot")).toBe("robot");

    expect(webRequestStateText("proposed")).toBe("待批准");
    expect(webRequestStateText("approved")).toBe("已批准，等待执行");
    expect(webRequestStateText("done")).toBe("已查完");
    expect(webRequestStateText("failed")).toBe("查失败");
    expect(webRequestStateText("rejected")).toBe("已拒绝");
    expect(webRequestStateText("weird")).toBe("weird");
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

  it("收下的过目产物按顺序带上种子 id（M12：`来源:id#条目id`）", () => {
    expect(
      buildStart("接着调研的结论写", [], ["exploration:x-1#H1", "discussion:d-1#C2"]),
    ).toEqual({
      input: "接着调研的结论写",
      material_ids: [],
      seed_ids: ["exploration:x-1#H1", "discussion:d-1#C2"],
    });
  });
});

describe("种子 id 构造器（M12 统一格式的唯一产出点）", () => {
  it("调研候选：exploration:探索id#候选id", () => {
    expect(explorationSeedId("x-1", "B2")).toBe("exploration:x-1#B2");
  });

  it("讨论卡：discussion:线程id#卡id", () => {
    expect(discussionSeedId("d-1", "C1")).toBe("discussion:d-1#C1");
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

  it("整份文档 + 焦点（M13「拿去验证」）：焦点落进 scope.text 的补充说明", () => {
    expect(
      buildExplorationBody({ kind: "document", focus: "导出按客户名筛会不会被滥用" }),
    ).toEqual({
      kind: "inquiry",
      scope: { kind: "document", ref: null, text: "导出按客户名筛会不会被滥用" },
    });
  });

  it("超长 focus 中央截断（码点 500 + …，镜像后端 cap）：任何长度都发得出去", () => {
    const long = `导${"出".repeat(600)}`;
    const body = buildExplorationBody({ kind: "entry", ref: "e1", focus: long });

    expect(Array.from(body.scope.text ?? "").length).toBeLessThanOrEqual(500);
    expect(body.scope.text?.endsWith("…")).toBe(true);
  });

  it("500 字以内原样不动（不截不断）", () => {
    const exact = "导".repeat(500);
    const body = buildExplorationBody({ kind: "entry", ref: "e1", focus: exact });

    expect(body.scope.text).toBe(exact);
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


// —— 搜索命中上的「带进本轮」（P2.8）——

function actionRound(over: Partial<RoundView> = {}): RoundView {
  return {
    round_id: "r1",
    project_id: "p1",
    user_input: "",
    step: "seats_running",
    waiting_on: "none",
    seat_round_count: 1,
    seats: [],
    active_seats: [],
    nominated_seats: [],
    available_seats: [],
    claims: [],
    seat_packets: [],
    dropped: [],
    questions: [],
    asked_questions: [],
    list_items: [],
    draft: null,
    advisories: [],
    failure_reason: null,
    failure_at_step: null,
    retry_from: null,
    analysis_basis: "",
    abandon_reason: null,
    confirmed_version_no: null,
    form_id: null,
    answer_sets: [],
    materials: [],
    ...over,
  } as RoundView;
}

function materialHit(over: Partial<SearchHitView> = {}): SearchHitView {
  return {
    kind: "material",
    label: "诊断报告.pdf",
    ref: "m1#c00",
    where: "材料 · 第 1 页",
    material_id: "m1",
    chunk_id: "m1#c00",
    ...over,
  };
}

describe("searchHitAction", () => {
  it("非材料命中不显示动作", () => {
    expect(
      searchHitAction(materialHit({ kind: "entry" }), actionRound(), false),
    ).toEqual({ visible: false });
  });

  it("看历史轮时不显示（带不进历史）", () => {
    expect(searchHitAction(materialHit(), actionRound(), true)).toEqual({
      visible: false,
    });
  });

  it("已在 materials 或 manual_chunks 里 → 禁用并说已在", () => {
    const inMaterials = actionRound({
      materials: [
        {
          material_id: "m1",
          title: "t",
          source_ref: "s",
          origin: "upload",
          parser_id: "p",
          chunk_count: 1,
          truncated: false,
          warnings: [],
          chunks: [
            { chunk_id: "m1#c00", text: "x", anchor: "第 1 页", eid: "E1" },
          ],
        },
      ],
    });
    expect(searchHitAction(materialHit(), inMaterials, false)).toMatchObject({
      visible: true,
      enabled: false,
      label: "已在本轮",
    });
    expect(
      searchHitAction(
        materialHit(),
        actionRound({ manual_chunks: [{ material_id: "m1", chunk_id: "m1#c00" }] }),
        false,
      ),
    ).toMatchObject({ visible: true, enabled: false, label: "已在本轮" });
  });

  it("草案卡之后禁用并说等下一轮", () => {
    expect(
      searchHitAction(materialHit(), actionRound({ step: "awaiting_confirm" }), false),
    ).toMatchObject({
      visible: true,
      enabled: false,
      reason: expect.stringContaining("下一轮"),
    });
  });

  it("可带料时启用", () => {
    expect(searchHitAction(materialHit(), actionRound(), false)).toEqual({
      visible: true,
      enabled: true,
      label: "带进本轮",
    });
  });

  it("材料命中跳不过去时，三种情况各说各的话", () => {
    const materials = [
      {
        material_id: "m1",
        title: "需求.md",
        source_ref: "s",
        origin: "upload",
        parser_id: "p",
        chunk_count: 1,
        truncated: false,
        warnings: [],
        chunks: [
          { chunk_id: "m1#c00", text: "x", anchor: "第 1 行", eid: "E1" },
        ],
      },
    ];
    expect(materialHitNotice("m1#c00", materials)).toContain("切到它所在的那一轮");
    expect(
      materialHitNotice("m1#c01", materials, [{ chunk_id: "m1#c01" }]),
    ).toContain("投影");
    expect(materialHitNotice("m1#c02", materials, [])).toContain(
      "还没带进任何一轮",
    );
  });

  it("联网检索结果不能手勾（后端会 422）", () => {
    expect(
      searchHitAction(
        materialHit({ origin: "web_search" }),
        actionRound(),
        false,
      ),
    ).toMatchObject({
      visible: true,
      enabled: false,
      reason: expect.stringContaining("研究入口"),
    });
  });
});
