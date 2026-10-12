// @vitest-environment jsdom
/* 复述卡上的"联网检索词"那一栏（工具层 C-2b）：
   默认不勾；勾一下把 query 递出去；服务端没配工具时不给复选框（但要说一句实话）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RoundView } from "../api/types";
import { OpeningCard } from "./OpeningCard";

afterEach(cleanup);

function round(over: Partial<RoundView> = {}): RoundView {
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
    web_queries: [
      { query: "RBAC 权限模型", why: "要查行业通行做法", chosen: false },
    ],
    web_search_available: true,
    ...over,
  } as unknown as RoundView;
}

function draw(over: Partial<Parameters<typeof OpeningCard>[0]> = {}) {
  const props = {
    round: round(),
    doc: null,
    edits: {},
    seats: [],
    webQueries: [] as string[],
    pending: false,
    onChangeClaim: vi.fn(),
    onToggleSeat: vi.fn(),
    onToggleWebQuery: vi.fn(),
    onSubmit: vi.fn(),
    ...over,
  };
  render(<OpeningCard {...props} />);
  return props;
}

describe("OpeningCard · 联网检索词", () => {
  it("默认不勾（联网要显式授权），但把「为什么查」写出来", () => {
    draw();

    const box = screen.getByRole("checkbox", { name: /RBAC 权限模型/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);
    expect(screen.getByText(/要查行业通行做法/)).toBeTruthy();
  });

  it("勾一下把这条检索词递出去", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("checkbox", { name: /RBAC 权限模型/ }));

    expect(props.onToggleWebQuery).toHaveBeenCalledWith("RBAC 权限模型");
  });

  it("已经勾上的读回来是勾着的", () => {
    draw({ webQueries: ["RBAC 权限模型"] });

    expect((screen.getByRole("checkbox", { name: /RBAC 权限模型/ }) as HTMLInputElement).checked).toBe(
      true,
    );
  });

  it("服务端没配联网工具：不给复选框，但说清「建议了、办不了」", () => {
    draw({ round: round({ web_search_available: false }) });

    expect(screen.queryByRole("checkbox", { name: /RBAC 权限模型/ })).toBeNull();
    expect(screen.getByText(/没配联网工具/)).toBeTruthy();
  });

  it("没提名检索词就整栏不出现", () => {
    draw({ round: round({ web_queries: [] }) });

    expect(screen.queryByText(/联网检索/)).toBeNull();
  });
});


it("显示分析者这一趟读了什么——批准主张之前看得见依据", () => {
  draw({ round: round({ analysis_basis: "读了 s5 与 s11 的内容" }) });
  expect(screen.getByText(/它这一趟读了/)).toBeTruthy();
  expect(screen.getByText(/s5 与 s11/)).toBeTruthy();
});

it("没有 analysis_basis 时不渲染那一行（老行回落空串）", () => {
  draw();
  expect(screen.queryByText(/它这一趟读了/)).toBeNull();
});
