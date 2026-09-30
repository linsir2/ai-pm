// @vitest-environment jsdom
/* 闭环区顶上那串"这个项目走过哪几轮"（F4）：点一行切过去只读看那一轮。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RoundHeadView } from "../api/types";
import { RoundHistory } from "./RoundHistory";

afterEach(cleanup);

const rounds: RoundHeadView[] = [
  {
    round_id: "r2",
    created_at: "2026-09-30T10:00:00+00:00",
    step: "done",
    user_input: "再加个导出权限",
    claims_count: 3,
    questions_count: 0,
    confirmed_version_no: 3,
  },
  {
    round_id: "r1",
    created_at: "2026-09-29T10:00:00+00:00",
    step: "done",
    user_input: "想做个订单导出",
    claims_count: 7,
    questions_count: 5,
    confirmed_version_no: 2,
  },
];

describe("RoundHistory", () => {
  it("收起时只报数，展开才列每一轮", () => {
    render(<RoundHistory rounds={rounds} currentRoundId="r2" onView={vi.fn()} />);

    expect(screen.getByText(/这个项目走过 2 轮/)).toBeTruthy();
    expect(screen.queryByText("想做个订单导出")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /这个项目走过 2 轮/ }));

    expect(screen.getByText("想做个订单导出")).toBeTruthy();
  });

  it("点一行把那个轮次 id 递出去（切过去只读看）", () => {
    const onView = vi.fn();
    render(<RoundHistory rounds={rounds} currentRoundId="r2" onView={onView} />);
    fireEvent.click(screen.getByRole("button", { name: /这个项目走过 2 轮/ }));

    fireEvent.click(screen.getByText("想做个订单导出"));

    expect(onView).toHaveBeenCalledWith("r1");
  });

  it("一行说清这一轮的量：主张 / 问卷 / 写入版本", () => {
    render(<RoundHistory rounds={rounds} currentRoundId="r2" onView={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /这个项目走过 2 轮/ }));

    expect(screen.getByText(/主张 7 · 问卷 5 · 写入 v2/)).toBeTruthy();
  });

  it("没有轮次就不占地方", () => {
    const { container } = render(
      <RoundHistory rounds={[]} currentRoundId={null} onView={vi.fn()} />,
    );

    expect(container.textContent).toBe("");
  });
});
