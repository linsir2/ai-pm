// @vitest-environment jsdom
/* 右列 composer（Codex 化）：BottomStrip 整块搬进 loop 底。
   RIPR：每条钉一个"搬运丢东西"的 fault——R=render 到该分支，I=fireEvent 触发，
   P=回调透传与否落到 DOM/事件，R=断言观察。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AwaitingMode } from "../api/payload";
import { LoopColumn } from "./LoopColumn";
import { LoopComposer } from "./LoopComposer";

afterEach(cleanup);

const here = dirname(fileURLToPath(import.meta.url));

function draw(over: Partial<Parameters<typeof LoopComposer>[0]> = {}) {
  const props = {
    projectId: "p1",
    composerMode: "input" as AwaitingMode,
    pending: null as string | null,
    drawerOpen: false,
    value: "",
    onChange: vi.fn(),
    onStartRound: vi.fn(),
    pendingSeedCount: 0,
    materials: [],
    onMaterialUploaded: vi.fn(),
    onMaterialRemove: vi.fn(),
    libraryCount: 0,
    onOpenLibrary: vi.fn(),
    onOpenDrawer: vi.fn(),
    ...over,
  };
  render(<LoopComposer {...props} />);
  return props;
}

describe("LoopComposer", () => {
  it("T1 空输入时提交禁用，点也发不出去（防空轮）", () => {
    const props = draw({ value: "" });

    const submit = screen.getByRole("button", { name: "开始这一轮" }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    expect(props.onStartRound).not.toHaveBeenCalled();
  });

  it("T2 有文本时提交透传 onStartRound（防接线断）", () => {
    const props = draw({ value: "做个导出" });

    fireEvent.click(screen.getByRole("button", { name: "开始这一轮" }));
    expect(props.onStartRound).toHaveBeenCalledTimes(1);
  });

  it("T3 问卷态且抽屉没开：'打开问卷'调 onOpenDrawer（防中断死路）", () => {
    const props = draw({ composerMode: "questionnaire", drawerOpen: false });

    fireEvent.click(screen.getByRole("button", { name: "打开问卷" }));
    expect(props.onOpenDrawer).toHaveBeenCalledTimes(1);
  });

  it("T4 草案态且抽屉开着：不给重复按钮，只留提示（防重复入口）", () => {
    draw({ composerMode: "draft", drawerOpen: true });

    expect(screen.queryByRole("button", { name: "打开草案卡" })).toBeNull();
    expect(screen.getByText(/在右边开着/)).toBeTruthy();
  });

  it("T5 失败态 composer 不再给重试按钮（收敛到状态行，防两处入口）", () => {
    draw({ composerMode: "failed" });

    expect(screen.queryByRole("button", { name: "接着跑这一轮" })).toBeNull();
  });

  it("T6 材料与种子透传渲染（防上下文丢失）", () => {
    draw({
      pendingSeedCount: 2,
      materials: [{ material_id: "m1", title: "扫描件" } as never],
    });

    expect(screen.getByText("扫描件")).toBeTruthy();
    expect(screen.getByText(/来自 2 条/)).toBeTruthy();
  });

  it("T9 收口：默认不给标语，hint 只说提交键（防设计语言堆叠）", () => {
    draw({ value: "做个导出" });

    expect(screen.queryByText(/一轮一轮长文档/)).toBeNull();
    expect(screen.queryByText(/先复述/)).toBeNull();
    expect(screen.getByText("⌘/Ctrl + Enter 提交")).toBeTruthy();
  });
});

describe("composer 页签隔离（T7，LoopColumn 层）", () => {
  function drawColumn(tab: "round" | "discussion" | "tray", research = vi.fn()) {
    const noop = vi.fn();
    render(
      <LoopColumn
        tab={tab}
        onTabChange={noop}
        round={null}
        doc={null}
        schema={null as never}
        mode="input"
        pending={null}
        drafts={{ collapsed: true } as never}
        approvedRequestIds={[]}
        allowWeb={false}
        onToggleWebRequest={noop}
        onToggleAllowWeb={noop}
        onDecideWebRequest={noop}
        pendingWebRequest={null}
        onSubmitOpening={noop}
        onSubmitAnswers={noop}
        onSkipAll={noop}
        onConfirm={noop}
        onDig={noop}
        onSimInquiry={noop}
        simOpen={false}
        simPending={false}
        onKeepSim={noop}
        onCloseSim={noop}
        onSimReview={noop}
        reviewOpen={false}
        reviewPending={false}
        onRunReview={noop}
        onCloseReview={noop}
        onRetry={noop}
        onAbandon={noop}
        onSkipSeat={noop}
        onViewRound={noop}
        onStartResearch={research}
        onRetryExploration={noop}
        onAbandonExploration={noop}
        onKeepSeed={noop}
        onKeepMaterial={noop}
        onDismissCandidate={noop}
        onKeepCard={noop}
        onDismissCard={noop}
        onVerifyCard={noop}
        onStartDiscussion={noop}
        onRunPanelRound={async () => null}
        onJoinPanel={noop}
        onAbortPanelRun={noop}
        onSayToThread={noop}
        onAssembleCards={noop}
        onKeepDiscussionCard={noop}
        onDismissDiscussionCard={noop}
        onCloseDiscussion={noop}
        onDiscussQuestion={noop}
        composerProjectId="p1"
        composerMode="input"
        composerPending={null}
        composerValue=""
        composerOnChange={noop}
        composerOnStartRound={noop}
      />,
    );
    return research;
  }

  it("T7a round 页签有 composer（防搬丢输入）", () => {
    drawColumn("round");

    expect(screen.getByLabelText("这一轮的想法")).toBeTruthy();
  });

  it("T7b discussion 页签无 composer（防跨页签误触开轮）", () => {
    drawColumn("discussion");

    expect(screen.queryByLabelText("这一轮的想法")).toBeNull();
  });

  it("T10 整份文档调研入口在讨论页签（M17：从灵感箱底部搬过来，防回归）", () => {
    const research = drawColumn("discussion");

    fireEvent.click(screen.getByRole("button", { name: "发起调研（整份文档）" }));
    expect(research).toHaveBeenCalledTimes(1);
  });

  it("T11 灵感箱页签没有发起按钮（入口只在讨论区）", () => {
    drawColumn("tray");

    expect(screen.queryByRole("button", { name: "发起调研（整份文档）" })).toBeNull();
  });
});

describe("LoopComposer 去悬浮结构（仿 tokens.test.ts 读样式源）", () => {
  it("T8 .loop 直属 .drawer 是 static，且 .foot 规则已删（防 overlay 残留盖住线程）", () => {
    const css = readFileSync(resolve(here, "../styles/app.css"), "utf8");
    const block = css.match(/\.loop\s*>\s*\.drawer\s*\{[^}]*\}/)?.[0] ?? "";
    expect(block).toMatch(/position\s*:\s*static/);
    expect(css).not.toMatch(/^\.foot\s*\{/m);
  });
});
