// @vitest-environment jsdom
/* 失败状态行（`P29` F8）：后端早就给了"失败在哪 + 重试从哪继续"，界面以前只写原因。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RoundView } from "../api/types";
import { RoundStatus } from "./RoundStatus";

afterEach(cleanup);

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

function draw(round_: RoundView) {
  const props = { onRetry: vi.fn(), onAbandon: vi.fn() };
  render(<RoundStatus round={round_} pending={null} {...props} />);
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

  it("放弃是终点：写原因，且**不给**重试按钮之外的动作", () => {
    const props = draw(round({ step: "abandoned", abandon_reason: "不要这一轮了" }));

    expect(screen.getByText(/^已放弃：不要这一轮了/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /继续\/重试/ }));
    expect(props.onRetry).toHaveBeenCalledTimes(1);
  });
});
