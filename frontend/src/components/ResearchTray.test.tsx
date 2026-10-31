// @vitest-environment jsdom
/* 研究页签（P3b-1）：输入/开始、结论与来源、来源卡挂当前轮、失败重跑。
   断言只用 getAttribute / textContent / disabled（本仓禁 jest-dom matcher）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ExplorationView, RoundView } from "../api/types";
import { ResearchTray } from "./ResearchTray";

afterEach(cleanup);

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
      answer: "同类产品多把批量导出上限设在 60 秒；超大文件需要分片。",
      answer_refs: ["E1"],
      findings: [{ finding_id: "F1", statement: "主流同类产品 60 秒上限", refs: ["E1"] }],
      sources: [
        {
          eid: "E1",
          title: "白皮书",
          url: "https://example.com/a",
          excerpt: "同类产品默认 60 秒上限。",
        },
      ],
      open_questions: ["要不要断点续传？"],
      notes: ["工具调用 2 次"],
    },
    ...over,
  };
}

const round = { round_id: "r-1", step: "seats_running", research_sources: [] } as unknown as RoundView;

function draw(over: Partial<Parameters<typeof ResearchTray>[0]> = {}) {
  const noop = vi.fn();
  const props = {
    runs: [run()],
    pending: false,
    topicLimit: 500,
    round,
    onStart: noop,
    onRetry: noop,
    onAbandon: noop,
    onAttach: noop,
    ...over,
  };
  render(<ResearchTray {...props} />);
  return props;
}

describe("ResearchTray", () => {
  it("画答案、结论、来源、开放问题与这一趟的实况", () => {
    draw();

    expect(
      screen.getByText(/同类产品多把批量导出上限设在 60 秒；超大文件需要分片。/),
    ).toBeTruthy();
    expect(screen.getByText(/主流同类产品 60 秒上限/)).toBeTruthy();
    expect(screen.getAllByText(/〔E1〕/).length).toBe(3); // 答案引用 + 结论引用 + 来源卡
    expect(screen.getByText("https://example.com/a")).toBeTruthy();
    expect(screen.getByText("要不要断点续传？")).toBeTruthy();
    expect(screen.getByText("工具调用 2 次")).toBeTruthy();
  });

  it("答案渲染在结论之前（段落）", () => {
    const { container } = render(
      <ResearchTray
        runs={[run()]}
        pending={false}
        topicLimit={500}
        round={round}
        onStart={vi.fn()}
        onRetry={vi.fn()}
        onAbandon={vi.fn()}
        onAttach={vi.fn()}
      />,
    );

    const text = container.textContent ?? "";
    expect(text.indexOf("同类产品多把批量导出上限")).toBeLessThan(
      text.indexOf("主流同类产品 60 秒上限"),
    );
  });

  it("没有 answer 的旧数据说清只找到来源", () => {
    draw({
      runs: [
        run({
          research: {
            ...run().research!,
            answer: undefined,
            answer_refs: undefined,
          },
        }),
      ],
    });

    expect(screen.getByText("这一趟只找到来源")).toBeTruthy();
  });

  it("开始研究：空主题按钮灰着，填了才调 onStart（带 trim 后的主题）", () => {
    const props = draw({ runs: [] });
    const start = screen.getByText("开始研究") as HTMLButtonElement;
    expect(start.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText("研究什么"), {
      target: { value: "  导出上限  " },
    });
    expect(start.disabled).toBe(false);
    fireEvent.click(start);

    expect(props.onStart).toHaveBeenCalledWith("导出上限");
  });

  it("别的动作在途：开始研究禁用并显示在途动作（S1 评审）", () => {
    const props = draw({ runs: [], busy: true, busyText: "正在讨论…" });

    fireEvent.change(screen.getByLabelText("研究什么"), {
      target: { value: "导出上限" },
    });

    const start = screen.getByText("正在讨论…") as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    fireEvent.click(start);
    expect(props.onStart).not.toHaveBeenCalled();
  });

  it("非 busy：填写后照常可用，文案仍是「开始研究」（S1 评审）", () => {
    const props = draw({ runs: [], busy: false, busyText: "正在处理…" });

    fireEvent.change(screen.getByLabelText("研究什么"), {
      target: { value: "导出上限" },
    });

    const start = screen.getByText("开始研究") as HTMLButtonElement;
    expect(start.disabled).toBe(false);
    fireEvent.click(start);
    expect(props.onStart).toHaveBeenCalledWith("导出上限");
  });

  it("已在跑时按钮灰着，并且允许放弃", () => {
    const props = draw({ runs: [run({ status: "running", research: null })] });

    expect(screen.getByText("研究进行中…")).toBeTruthy();
    expect((screen.getByText("开始研究") as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByText("放弃"));
    expect(props.onAbandon).toHaveBeenCalledWith("x-1");
  });

  it("来源卡：能挂时按钮可点，挂了之后写「已挂到当前轮」", () => {
    const props = draw();

    fireEvent.click(screen.getByText("挂到当前轮"));
    expect(props.onAttach).toHaveBeenCalledWith("x-1", ["E1"]);
  });

  it("已经挂过的来源不再可点", () => {
    draw({
      round: {
        round_id: "r-1",
        step: "seats_running",
        research_sources: [{ eid: "E1" }],
      } as unknown as RoundView,
    });

    const button = screen.getByText("已挂到当前轮") as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("没有可挂的轮次时说清原因", () => {
    draw({ round: null });

    expect(screen.getByText("先开一轮，再挂来源")).toBeTruthy();
  });

  it("失败的研究给重跑按钮并带上原因", () => {
    const props = draw({
      runs: [run({ status: "failed", failure_reason: "没有取到任何来源", research: null })],
    });

    expect(screen.getByText(/失败：没有取到任何来源/)).toBeTruthy();
    fireEvent.click(screen.getByText("重跑"));
    expect(props.onRetry).toHaveBeenCalledWith("x-1");
  });
});
