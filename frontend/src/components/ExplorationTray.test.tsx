// @vitest-environment jsdom
/* 过目台（M8）：进行中 / 失败（重试、放弃）/ 候选卡三个动作 / 超限提示。
   动作按钮的参数按"探索 id + 整条候选"给，上层拿它直连后端。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CandidateView, ExplorationView } from "../api/types";
import { ExplorationTray } from "./ExplorationTray";

afterEach(cleanup);

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

function draw(over: Partial<Parameters<typeof ExplorationTray>[0]> = {}) {
  const props = {
    explorations: [exploration()],
    pending: false,
    onRetry: vi.fn(),
    onAbandon: vi.fn(),
    onKeepSeed: vi.fn(),
    onKeepMaterial: vi.fn(),
    onDismiss: vi.fn(),
    onStartDocument: vi.fn(),
    ...over,
  };
  render(<ExplorationTray {...props} />);
  return props;
}

describe("ExplorationTray", () => {
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

  it("三个动作各自把探索 id 与整条候选交出去", () => {
    const props = draw();
    const item = exploration();

    fireEvent.click(screen.getByRole("button", { name: "收下为下一轮输入" }));
    expect(props.onKeepSeed).toHaveBeenCalledWith("x-1", item.candidates[0]);

    fireEvent.click(screen.getByRole("button", { name: "收下为材料" }));
    expect(props.onKeepMaterial).toHaveBeenCalledWith("x-1", item.candidates[0]);

    fireEvent.click(screen.getByRole("button", { name: "删掉" }));
    expect(props.onDismiss).toHaveBeenCalledWith("x-1", item.candidates[0]);
  });

  it("已收下 / 已删掉的候选不再上台（也没有动作按钮）", () => {
    draw({
      explorations: [
        exploration({
          candidates: [
            candidate({ candidate_id: "H1", state: "kept", kept_as: "seed" }),
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

  it("有调研在跑时不能再发起（同项目同时只允许一趟）", () => {
    draw({
      explorations: [exploration({ status: "running", candidates: [] })],
    });

    const start = screen.getByRole("button", { name: "发起调研（整份文档）" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(screen.getByText(/调研在跑/)).toBeTruthy();
  });

  it("点『发起调研（整份文档）』调到 onStartDocument", () => {
    const props = draw({ explorations: [] });

    fireEvent.click(screen.getByRole("button", { name: "发起调研（整份文档）" }));

    expect(props.onStartDocument).toHaveBeenCalledTimes(1);
  });

  it("有动作在途时重试 / 放弃点不下去（跑中放弃走不进后端）", () => {
    const props = draw({
      explorations: [
        exploration({ status: "failed", failure_reason: "模型调用超时", candidates: [] }),
      ],
      pending: true,
    });

    expect(
      (screen.getByRole("button", { name: "重试" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByRole("button", { name: "放弃" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(props.onAbandon).not.toHaveBeenCalled();
  });
});
