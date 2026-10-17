// @vitest-environment jsdom
/* 右栏任务驱动显隐（06 §2.4 §3）：无任务时折叠为 56px rail（竖按钮 + 徽标数），
   点开展开；展开态页签行有「收起」（手动覆盖）。

   构造照抄 LoopComposer.test.tsx 的 drawColumn（全量 noop props，
   `drafts` 按 `{ collapsed: true } as never` 短写）；断言只用
   getAttribute+toBe / textContent+toContain / 元素非空（禁 jest-dom matcher）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AwaitingMode } from "../api/payload";
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
    ...over,
  };
  render(<LoopColumn {...props} />);
  return props;
}

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
