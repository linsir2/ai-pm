// @vitest-environment jsdom
/* 状态行（2026-10-14 轻量化）：素行 + 条件按钮。
   按钮只在**真能点动**时出现——`RESUME_ROUTES`（后端 `graph/graph.py`）里
   claims_ready / awaiting_answers / awaiting_confirm / done / abandoned 全 → END，
   这些 step 上的「接着跑」是死动作。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RoundView } from "../api/types";
import { RoundStatus } from "./RoundStatus";

afterEach(cleanup);

const here = dirname(fileURLToPath(import.meta.url));

function round(over: Partial<RoundView> = {}): RoundView {
  return {
    round_id: "r1",
    step: "awaiting_confirm",
    waiting_on: "draft_card",
    seat_round_count: 1,
    failure_reason: null,
    failure_at_step: null,
    retry_from: null,
    abandon_reason: null,
    ...over,
  } as unknown as RoundView;
}

function draw(round_: RoundView, pending: string | null = null) {
  const props = { onRetry: vi.fn(), onAbandon: vi.fn() };
  render(<RoundStatus round={round_} pending={pending} {...props} />);
  return props;
}

describe("RoundStatus", () => {
  it("正常一轮不写失败那一行", () => {
    draw(round());

    expect(screen.queryByText(/^失败：/)).toBeNull();
  });

  it("失败时写清「失败在哪 + 点重试会从哪继续」", () => {
    draw(
      round({
        step: "failed",
        failure_reason: "整合两次都过不了校验",
        failure_at_step: "seats_running",
        retry_from: "从席位轮继续（已交回的不重跑）",
      }),
    );

    const line = screen.getByText(/^失败：/);
    expect(line.textContent).toContain("整合两次都过不了校验");
    expect(line.textContent).toContain("从席位轮继续");
  });

  it("失败态给「接着跑」并透传 onRetry（失败的唯一出口）", () => {
    const props = draw(round({ step: "failed", failure_reason: "模型超时" }));

    fireEvent.click(screen.getByRole("button", { name: "接着跑这一轮" }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
  });

  it("写完了不给「接着跑」（done → END 是死动作，防假按钮）", () => {
    draw(round({ step: "done", waiting_on: "none" }));

    expect(screen.queryByRole("button", { name: "接着跑这一轮" })).toBeNull();
  });

  it("席位在跑：不给「接着跑」（点不动），但「放弃」在", () => {
    draw(round({ step: "seats_running", waiting_on: "none" }), "seat");

    expect(screen.queryByRole("button", { name: "接着跑这一轮" })).toBeNull();
    expect(screen.getByRole("button", { name: "放弃这一轮" })).toBeTruthy();
  });

  it("放弃是终点：写原因，且不给任何动作（abandoned → END）", () => {
    draw(round({ step: "abandoned", abandon_reason: "不要这一轮了" }));

    expect(screen.getByText(/^已放弃：不要这一轮了/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("样式守卫：状态行不再是边框盒、七格链子样式已删（防回潮）", () => {
    const css = readFileSync(resolve(here, "../styles/app.css"), "utf8");
    const block = css.match(/\.statusline\s*\{[^}]*\}/)?.[0] ?? "";

    expect(block).not.toBe("");
    expect(block).not.toMatch(/border\s*:/);
    expect(css).not.toMatch(/^\.steps\s*\{/m);
  });
});
