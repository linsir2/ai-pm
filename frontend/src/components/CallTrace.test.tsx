// @vitest-environment jsdom
/* 调用轨迹（可观测性 v2）：折叠标题露计数、空数据明说"没有"、展开是时间序表格。 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import type { RoundCalls } from "../api/client";
import { CallTrace } from "./CallTrace";

function jsonResponse(body: unknown): Partial<Response> {
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    text: async () => JSON.stringify(body),
    json: async () => body,
  };
}

function stub(calls: RoundCalls): void {
  globalThis.fetch = (async () => jsonResponse(calls)) as unknown as typeof fetch;
}

const originalFetch = globalThis.fetch;

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

it("折叠标题露出模型与工具的次数", async () => {
  stub({
    round_id: "r1",
    calls: [],
    model_calls: 5,
    model_failed: 1,
    tool_calls: 3,
    tool_failed: 0,
  });
  render(<CallTrace roundId="r1" />);

  expect(await screen.findByText("调用轨迹 · 模型 5 次 / 工具 3 次")).toBeTruthy();
});

it("空数据显示「这一轮还没有调用」——不造假数据", async () => {
  stub({
    round_id: "r1",
    calls: [],
    model_calls: 0,
    model_failed: 0,
    tool_calls: 0,
    tool_failed: 0,
  });
  render(<CallTrace roundId="r1" />);

  expect(await screen.findByText(/这一轮还没有调用/)).toBeTruthy();
});

it("展开后按时间序列出每一次调用（含失败分类）", async () => {
  stub({
    round_id: "r1",
    calls: [
      {
        kind: "model",
        name: "claims",
        duration_ms: 10,
        ok: true,
        error_kind: null,
        at: "2026-10-23T10:00:00+00:00",
      },
      {
        kind: "tool",
        name: "web_search",
        duration_ms: 900,
        ok: false,
        error_kind: "timeout",
        at: "2026-10-23T10:00:01+00:00",
      },
    ],
    model_calls: 1,
    model_failed: 0,
    tool_calls: 1,
    tool_failed: 1,
  });
  const { container } = render(<CallTrace roundId="r1" />);

  expect(await screen.findByText(/调用轨迹/)).toBeTruthy();
  const rows = container.querySelectorAll(".call-trace tbody tr");
  expect(rows).toHaveLength(2);
  expect(rows[0]!.textContent).toContain("claims");
  expect(rows[0]!.textContent).toContain("成功");
  expect(rows[1]!.textContent).toContain("web_search");
  expect(rows[1]!.textContent).toContain("失败");
  expect(rows[1]!.textContent).toContain("timeout");
});
