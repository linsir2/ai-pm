// @vitest-environment jsdom
/* 右栏任务驱动显隐（06 §2.4 §3）：无任务时折叠为 56px rail（竖按钮 + 徽标数），
   点开展开；展开态页签行有「收起」（手动覆盖）。

   构造照抄 LoopComposer.test.tsx 的 drawColumn（全量 noop props，
   `drafts` 按 `{ collapsed: true } as never` 短写）；断言只用
   getAttribute+toBe / textContent+toContain / 元素非空（禁 jest-dom matcher）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AwaitingMode } from "../api/payload";
import type { RoundView } from "../api/types";
import { LoopColumn } from "./LoopColumn";
import type { LoopTab } from "./LoopColumn";

afterEach(cleanup);

function drawColumn(over: Partial<Parameters<typeof LoopColumn>[0]> = {}) {
  const noop = vi.fn();
  const props = {
    tab: "round" as LoopTab,
    onTabChange: noop,
    round: null,
    doc: null,
    schema: null as never,
    mode: "input" as AwaitingMode,
    pending: null,
    drafts: { collapsed: true } as never,
    onSubmitOpening: noop,
    onSubmitAnswers: noop,
    onSkipAll: noop,
    onConfirm: noop,
    onDig: noop,
    onSimInquiry: noop,
    simOpen: false,
    simPending: false,
    onKeepSim: noop,
    onCloseSim: noop,
    onSimReview: noop,
    reviewOpen: false,
    reviewPending: false,
    onRunReview: noop,
    onCloseReview: noop,
    onRetry: noop,
    onAbandon: noop,
    onSkipSeat: noop,
    onViewRound: noop,
    onStartResearch: noop,
    onRetryExploration: noop,
    onAbandonExploration: noop,
    onKeepSeed: noop,
    onKeepMaterial: noop,
    onDismissCandidate: noop,
    onKeepCard: noop,
    onDismissCard: noop,
    onVerifyCard: noop,
    onStartWebResearch: noop,
    onRetryResearch: noop,
    onAbandonResearch: noop,
    onAttachResearchSources: noop,
    onStartDiscussion: noop,
    onRunPanelRound: async () => null,
    onJoinPanel: noop,
    onAbortPanelRun: noop,
    onSayToThread: noop,
    onAssembleCards: noop,
    onKeepDiscussionCard: noop,
    onDismissDiscussionCard: noop,
    onCloseDiscussion: noop,
    onDiscussQuestion: noop,
    composerProjectId: "p1",
    composerMode: "input" as AwaitingMode,
    composerPending: null,
    composerValue: "",
    composerOnChange: noop,
    composerOnStartRound: noop,
    approvedRequestIds: [] as string[],
    allowWeb: false,
    onToggleWebRequest: noop,
    onToggleAllowWeb: noop,
    onDecideWebRequest: noop,
    pendingWebRequest: null as { requestId: string; decision: "approve" | "reject" } | null,
    ...over,
  };
  render(<LoopColumn {...props} />);
  return props;
}

function openingRound(over: Partial<RoundView> = {}): RoundView {
  return {
    round_id: "r1",
    project_id: "p1",
    user_input: "想做个订单导出",
    step: "claims_ready",
    waiting_on: "opening_card",
    seat_round_count: 0,
    seats: [],
    active_seats: [],
    nominated_seats: [],
    available_seats: [],
    claims: [],
    seat_packets: [],
    dropped: [],
    questions: [],
    list_items: [],
    draft: null,
    advisories: [],
    failure_reason: null,
    abandon_reason: null,
    confirmed_version_no: null,
    form_id: null,
    answer_sets: [],
    materials: [],
    web_queries: [],
    web_requests: [
      {
        request_id: "w1",
        query: "RBAC 权限模型",
        why: "要查行业通行做法",
        origin: "analyzer",
        seat_id: null,
        pass_index: null,
        state: "proposed",
        result_note: null,
        created_at: null,
      },
    ],
    allow_web: false,
    web_search_available: true,
    web_search_network_access: true,
    ...over,
  } as unknown as RoundView;
}

const openingDrafts = {
  collapsed: false,
  edits: {},
  seats: [],
  webQueries: [],
  approvedRequestIds: [],
  allowWeb: false,
  answers: {},
  accepted: [],
  memoryAccepted: [],
  changeClaim: vi.fn(),
  toggleSeat: vi.fn(),
  toggleWebQuery: vi.fn(),
  toggleWebRequest: vi.fn(),
  setAllowWeb: vi.fn(),
  toggleAccepted: vi.fn(),
  setAccepted: vi.fn(),
  toggleMemory: vi.fn(),
  changeAnswer: vi.fn(),
  setCollapsed: vi.fn(),
};

const schemaWithLimits = {
  limits: {
    max_seat_rounds: 3,
    max_questionnaires: 2,
    max_open_items_per_batch: 3,
    max_web_executions: 3,
  },
} as never;

describe("LoopColumn 折叠态（rail，06 §2.4 §3）", () => {
  it("折叠态只剩 rail：按钮含徽标数，不渲染页签/composer", () => {
    const onExpand = vi.fn();
    drawColumn({ collapsed: true, badgeCount: 3, onExpand });

    const rail = screen.getByRole("button", { name: /展开闭环区/ });
    expect(rail).toBeTruthy();
    expect(rail.textContent).toContain("3");
    expect(screen.queryByRole("tablist")).toBeNull();
    expect(screen.queryByLabelText("这一轮的想法")).toBeNull();

    fireEvent.click(rail);
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it("徽标 0 时按钮不带数字", () => {
    drawColumn({ collapsed: true, badgeCount: 0, onExpand: vi.fn() });

    const rail = screen.getByRole("button", { name: /展开闭环区/ });
    expect(rail).toBeTruthy();
    expect(rail.textContent).not.toContain("·");
  });

  it("展开态页签行有「收起」：点它调 onCollapse（手动覆盖）", () => {
    const onCollapse = vi.fn();
    drawColumn({ onCollapse });

    fireEvent.click(screen.getByRole("button", { name: /收起/ }));
    expect(onCollapse).toHaveBeenCalledTimes(1);
  });

  it("没给 onCollapse 就不出收起按钮（老消费者不受影响）", () => {
    drawColumn({});

    expect(screen.queryByRole("button", { name: /收起/ })).toBeNull();
  });
});

describe("LoopColumn · 对话流与决策卡浮层（2026-10-29）", () => {
  it("opening 模式不再整栏独占：流和复述卡同时渲染", () => {
    drawColumn({
      round: openingRound(),
      mode: "opening",
      drafts: { ...openingDrafts } as never,
    });

    // 流还在（本轮输入的引文可见），卡以浮层弹出
    expect(screen.getByText(/本轮输入/)).toBeTruthy();
    expect(screen.getByText("想做个订单导出")).toBeTruthy();
    expect(screen.getByText(/复述 · 我读到的/)).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "复述卡" })).toBeTruthy();
    // 收起按钮来自 onCollapse
    expect(screen.getByRole("button", { name: "收起复述卡" })).toBeTruthy();
  });

  it("收起复述卡后：卡消失，header 出现 chip，点 chip 重开", () => {
    const setCollapsed = vi.fn();
    drawColumn({
      round: openingRound(),
      mode: "opening",
      drafts: { ...openingDrafts, collapsed: true, setCollapsed } as never,
    });

    expect(screen.queryByRole("dialog", { name: "复述卡" })).toBeNull();
    const chip = screen.getByRole("button", { name: /复述卡 · 待你提交/ });
    fireEvent.click(chip);
    expect(setCollapsed).toHaveBeenCalledWith(false);
  });

  it("waiting_on 翻页（提交后）浮层自动收，chip 也消失", () => {
    drawColumn({
      round: openingRound({ waiting_on: "none", step: "seats_running" }),
      mode: "loading",
      drafts: { ...openingDrafts, collapsed: true } as never,
    });

    expect(screen.queryByRole("dialog", { name: "复述卡" })).toBeNull();
    expect(screen.queryByRole("button", { name: /复述卡 · 待你提交/ })).toBeNull();
  });

  it("opening 模式下 composer 仍可起草（输入不禁），提交按钮锁着", () => {
    const composerOnChange = vi.fn();
    drawColumn({
      round: openingRound(),
      mode: "opening",
      composerOnChange,
      composerValue: "",
      drafts: { ...openingDrafts } as never,
    });

    const box = screen.getByLabelText("这一轮的想法") as HTMLTextAreaElement;
    expect(box.disabled).toBe(false);
    fireEvent.change(box, { target: { value: "下一轮想加权限" } });
    expect(composerOnChange).toHaveBeenCalledWith("下一轮想加权限");
  });
});

describe("LoopColumn · 联网授权与决策（P3a）", () => {
  it("开场卡：申请勾选与 allow_web 都接到 props 的动作上", () => {
    const onToggleWebRequest = vi.fn();
    const onToggleAllowWeb = vi.fn();
    drawColumn({
      round: openingRound(),
      mode: "opening",
      schema: schemaWithLimits,
      drafts: { ...openingDrafts } as never,
      onToggleWebRequest,
      onToggleAllowWeb,
    });

    fireEvent.click(screen.getByRole("checkbox", { name: /RBAC 权限模型/ }));
    expect(onToggleWebRequest).toHaveBeenCalledWith("w1");

    fireEvent.click(screen.getByRole("checkbox", { name: /自动批准/ }));
    expect(onToggleAllowWeb).toHaveBeenCalledWith(true);
  });

  it("开场卡读 props 里的已勾 id 与 allowWeb", () => {
    drawColumn({
      round: openingRound(),
      mode: "opening",
      schema: schemaWithLimits,
      drafts: { ...openingDrafts } as never,
      approvedRequestIds: ["w1"],
      allowWeb: true,
    });

    expect(
      (screen.getByRole("checkbox", { name: /RBAC 权限模型/ }) as HTMLInputElement).checked,
    ).toBe(true);
    expect(
      (screen.getByRole("checkbox", { name: /自动批准/ }) as HTMLInputElement).checked,
    ).toBe(true);
  });

  it("本轮时间线：批准把 request_id 与 decision 交给 onDecideWebRequest", () => {
    const onDecideWebRequest = vi.fn();
    drawColumn({
      round: openingRound({ waiting_on: "none", step: "seats_running" }),
      mode: "loading",
      schema: {
        limits: {
          max_seat_rounds: 3,
          max_questionnaires: 2,
          max_open_items_per_batch: 3,
          max_web_executions: 3,
        },
      } as never,
      onDecideWebRequest,
    });

    fireEvent.click(screen.getByRole("button", { name: "批准并查" }));
    expect(onDecideWebRequest).toHaveBeenCalledWith("w1", "approve");
  });
});
