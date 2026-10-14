// @vitest-environment jsdom
/* 灵感箱（M12）：讨论卡 + 调研候选统一过目。
   进行中 / 失败（重试、放弃）/ 候选卡三动作 / 卡三动作 + 聊聊 / 超限提示。
   动作按钮的参数按"来源 id + 整条条目"给，上层拿它直连后端。 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CardView, CandidateView, ExplorationView, ThreadView } from "../api/types";
import { InboxTray } from "./InboxTray";

afterEach(cleanup);

function card(over: Partial<CardView> = {}): CardView {
  return {
    card_id: "C1",
    statement: "导出先只做全量，筛选等有人骂再加",
    grounding: "invented",
    based_on: [],
    kept_ref: null,
    state: "new",
    kept_as: null,
    ...over,
  };
}

function thread(over: Partial<ThreadView> = {}): ThreadView {
  return {
    thread_id: "d-1",
    project_id: "p1",
    topic: "导出要不要做筛选",
    question: null,
    role_id: "dlg.challenger",
    role_title: "反方",
    status: "open",
    turns: [],
    cards: [],
    findings_count: 0,
    created_at: "2026-10-05T10:00:00+00:00",
    ...over,
  };
}

function candidate(over: Partial<CandidateView> = {}): CandidateView {
  return {
    candidate_id: "H1",
    kind: "hypothesis",
    statement: "导出要支持按状态筛选",
    state: "new",
    kept_as: null,
    kept_ref: null,
    grounding: "document",
    based_on: ["E1"],
    falsification: "如果用户只导全量，就不需要筛选",
    evidence_needed: null,
    next_step: null,
    ...over,
  };
}

function exploration(over: Partial<ExplorationView> = {}): ExplorationView {
  return {
    exploration_id: "x-1",
    project_id: "p1",
    kind: "inquiry",
    status: "done",
    scope_kind: "entry",
    scope_ref: "e1",
    scope_text: "导出要有筛选",
    created_at: "2026-10-05T10:00:00+00:00",
    failure_reason: null,
    abandon_reason: null,
    overflow_count: 0,
    candidates: [candidate()],
    ...over,
  };
}

function draw(over: Partial<Parameters<typeof InboxTray>[0]> = {}) {
  const props = {
    threads: [] as ThreadView[],
    explorations: [exploration()],
    pending: false,
    onRetry: vi.fn(),
    onAbandon: vi.fn(),
    onKeepSeed: vi.fn(),
    onKeepMaterial: vi.fn(),
    onDismiss: vi.fn(),
    onKeepCard: vi.fn(),
    onDismissCard: vi.fn(),
    onDiscuss: vi.fn(),
    onVerify: vi.fn(),
    ...over,
  };
  render(<InboxTray {...props} />);
  return props;
}

describe("InboxTray", () => {
  it("空态说人话", () => {
    draw({ explorations: [] });

    expect(screen.getByText(/还没有可过目的东西/)).toBeTruthy();
  });

  it("候选卡：kind 标签、原文、证伪与依据都摆出来", () => {
    draw();

    expect(screen.getByText("假设")).toBeTruthy();
    expect(screen.getByText("导出要支持按状态筛选")).toBeTruthy();
    expect(screen.getByText(/证伪它：如果用户只导全量/)).toBeTruthy();
  });

  it("候选三个动作各自把探索 id 与整条候选交出去（忽略不再叫删掉）", () => {
    const props = draw();
    const item = exploration();

    fireEvent.click(screen.getByRole("button", { name: "收下为下一轮输入" }));
    expect(props.onKeepSeed).toHaveBeenCalledWith("x-1", item.candidates[0]);

    fireEvent.click(screen.getByRole("button", { name: "收下为材料" }));
    expect(props.onKeepMaterial).toHaveBeenCalledWith("x-1", item.candidates[0]);

    fireEvent.click(screen.getByRole("button", { name: "忽略" }));
    expect(props.onDismiss).toHaveBeenCalledWith("x-1", item.candidates[0]);
  });

  it("讨论卡：原文、接地标签、来自哪条讨论都摆出来，三动作 + 聊聊各交各的", () => {
    const props = draw({
      explorations: [],
      threads: [
        thread({
          cards: [
            card({ card_id: "C1", grounding: "invented" }),
            card({
              card_id: "C2",
              statement: "有据的一条",
              grounding: "document",
              based_on: ["E1"],
            }),
          ],
        }),
      ],
    });

    expect(screen.getByText("凭空推演")).toBeTruthy();
    expect(screen.getByText("文档里有据")).toBeTruthy();
    expect(screen.getAllByText(/来自讨论：导出要不要做筛选/)).toHaveLength(2);

    const first = screen.getAllByRole("article")[0]!;
    fireEvent.click(within(first).getByRole("button", { name: "收下为下一轮输入" }));
    expect(props.onKeepCard).toHaveBeenLastCalledWith("d-1", "C1", "seed");

    fireEvent.click(within(first).getByRole("button", { name: "收下为材料" }));
    expect(props.onKeepCard).toHaveBeenLastCalledWith("d-1", "C1", "material");

    fireEvent.click(within(first).getByRole("button", { name: "忽略" }));
    expect(props.onDismissCard).toHaveBeenCalledWith("d-1", "C1");

    fireEvent.click(within(first).getByRole("button", { name: "聊聊" }));
    expect(props.onDiscuss).toHaveBeenCalledWith("导出先只做全量，筛选等有人骂再加");
  });

  it("候选也要能「聊聊」（带着它的原话开线程）", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("button", { name: "聊聊" }));

    expect(props.onDiscuss).toHaveBeenCalledWith("导出要支持按状态筛选");
  });

  it("讨论卡「拿去验证」（M13）：把卡片原话交给上层去起聚焦调研", () => {
    const props = draw({
      explorations: [],
      threads: [thread({ cards: [card()] })],
    });

    fireEvent.click(screen.getByRole("button", { name: "拿去验证" }));

    expect(props.onVerify).toHaveBeenCalledWith("导出先只做全量，筛选等有人骂再加");
  });

  it("调研范围带焦点说明时显示出来（认得出这趟是验证谁）", () => {
    draw({
      explorations: [
        exploration({
          scope_kind: "document",
          scope_ref: null,
          scope_text: "导出按客户名筛会不会被滥用",
        }),
      ],
    });

    expect(screen.getByText(/整份文档 · 导出按客户名筛会不会被滥用/)).toBeTruthy();
  });

  it("已处理的条目不再上台：收下/忽略的卡、已收/已删的候选都没有", () => {
    draw({
      threads: [
        thread({
          cards: [
            card({ card_id: "C1", state: "kept", kept_as: "seed" }),
            card({ card_id: "C2", state: "dismissed" }),
          ],
        }),
      ],
      explorations: [
        exploration({
          candidates: [
            candidate({ candidate_id: "H1", state: "kept", kept_as: "material", kept_ref: "m-1" }),
            candidate({ candidate_id: "B1", state: "dismissed" }),
          ],
        }),
      ],
    });

    expect(screen.getByText(/还没有可过目的东西/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "收下为下一轮输入" })).toBeNull();
  });

  it("失败的调研：显示原因，重试 / 放弃各把 id 交出去", () => {
    const props = draw({
      explorations: [
        exploration({ status: "failed", failure_reason: "模型调用超时", candidates: [] }),
      ],
    });

    expect(screen.getByText("模型调用超时")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(props.onRetry).toHaveBeenCalledWith("x-1");

    fireEvent.click(screen.getByRole("button", { name: "放弃" }));
    expect(props.onAbandon).toHaveBeenCalledWith("x-1");
  });

  it("overflow_count > 0 时照实说截断了几条", () => {
    draw({ explorations: [exploration({ overflow_count: 3 })] });

    expect(screen.getByText("超出上限，已截断 3 条")).toBeTruthy();
  });

  it("发起按钮已搬去讨论区：箱底不再有「发起调研（整份文档）」（M17 需求变更）", () => {
    draw({
      explorations: [exploration({ status: "running", candidates: [] })],
    });

    expect(screen.queryByRole("button", { name: "发起调研（整份文档）" })).toBeNull();
    expect(screen.getByText(/调研在跑/)).toBeTruthy();
  });

  it("有动作在途时全部点不动（防连点）", () => {
    const props = draw({
      threads: [thread({ cards: [card()] })],
      explorations: [
        exploration({ status: "failed", failure_reason: "模型调用超时", candidates: [] }),
      ],
      pending: true,
    });

    expect((screen.getByRole("button", { name: "重试" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "放弃" }) as HTMLButtonElement).disabled).toBe(true);
    expect(
      (screen.getByRole("button", { name: "收下为下一轮输入" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect((screen.getByRole("button", { name: "拿去验证" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(props.onAbandon).not.toHaveBeenCalled();
  });
});
