// @vitest-environment jsdom
/* 留样口径：零记录不能推出零调用，计数要与已记录的表格对应。 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { RoundCalls } from "../api/client";
import { CallTrace } from "./CallTrace";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("零记录只说明没有留样，不声称这一轮没有实际调用", async () => {
  const calls: RoundCalls = {
    round_id: "r-empty",
    calls: [],
    model_calls: 0,
    model_failed: 0,
    tool_calls: 0,
    tool_failed: 0,
  };
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify(calls)));
  render(<CallTrace roundId="r-empty" />);

  const empty = await screen.findByText(/这一轮还没有调用/);
  expect(empty.textContent).toContain("这一轮还没有调用记录");
  expect(empty.textContent).toContain("可能未开启留样");
  expect(empty.textContent).toContain("不能据此判断实际调用次数");
  expect(screen.queryByText(/^这一轮还没有调用[。！]?$/)).toBeNull();
});

it("非空计数明确标为已记录，展开后仍展示模型与工具记录", async () => {
  const calls: RoundCalls = {
    round_id: "r-recorded",
    calls: [
      {
        kind: "model",
        name: "claims",
        duration_ms: 10,
        ok: true,
        error_kind: null,
        at: "2026-10-30T10:00:00+00:00",
      },
      {
        kind: "tool",
        name: "web_search",
        duration_ms: 900,
        ok: false,
        error_kind: "timeout",
        at: "2026-10-30T10:00:01+00:00",
      },
    ],
    model_calls: 1,
    model_failed: 0,
    tool_calls: 1,
    tool_failed: 1,
  };
  vi.stubGlobal("fetch", async () => new Response(JSON.stringify(calls)));
  render(<CallTrace roundId="r-recorded" />);

  const heading = await screen.findByText("调用轨迹 · 模型 1 次 / 工具 1 次");
  const summary = heading.closest("summary")!;
  expect(summary.textContent).toContain("已记录");
  fireEvent.click(summary);

  const rows = within(screen.getByRole("table")).getAllByRole("row");
  expect(rows).toHaveLength(3);
  expect(rows[1]!.textContent).toContain("claims");
  expect(rows[1]!.textContent).toContain("成功");
  expect(rows[2]!.textContent).toContain("web_search");
  expect(rows[2]!.textContent).toContain("失败");
  expect(rows[2]!.textContent).toContain("timeout");
});
