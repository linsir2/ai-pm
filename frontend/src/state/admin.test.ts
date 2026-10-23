/* 管理后台的形状层（`06` §2.4）。这些是纯函数，测的就是"形状对不对"。 */

import { expect, it } from "vitest";

import type { AdminStatsRow } from "../api/client";
import {
  ADMIN_SECTIONS,
  actionsFor,
  approvalText,
  compactNumber,
  durationText,
  lastSeenText,
  overviewOf,
  sectionRows,
  showSection,
} from "./admin";

function row(over: Partial<AdminStatsRow> = {}): AdminStatsRow {
  return {
    user_id: "u1",
    username: "alice",
    role: "user",
    status: "active",
    projects: 0,
    rounds: 0,
    calls: 0,
    failed: 0,
    avg_duration_ms: null,
    tool_calls: 0,
    tool_failed: 0,
    tool_avg_duration_ms: null,
    total_tokens: 0,
    active_sessions: 0,
    last_seen_at: null,
    created_at: "2026-10-16T00:00:00+00:00",
    approved_at: null,
    approved_by: null,
    ...over,
  };
}

const labels = (r: AdminStatsRow) => actionsFor(r).map((a) => a.label);

it("admin 行一个动作都不给——不给死按钮", () => {
  // 后端 `admin_protected` 硬拦，界面再给一个必然报错的按钮只会让人以为坏了
  expect(labels(row({ role: "admin" }))).toEqual([]);
  expect(labels(row({ role: "admin", status: "pending" }))).toEqual([]);
});

it("pending 行只给批准与拒绝——封禁一个还没生效的人没有意义", () => {
  expect(labels(row({ status: "pending" }))).toEqual(["批准", "拒绝"]);
});

it("active 给封禁", () => {
  expect(labels(row({ status: "active" }))).toEqual(["封禁"]);
});

it("已拒绝与封禁中都能一键恢复（误操作不必不可逆）", () => {
  expect(labels(row({ status: "rejected" }))).toEqual(["恢复"]);
  expect(labels(row({ status: "banned" }))).toEqual(["恢复"]);
});

it("按状态分区，保持传入顺序", () => {
  const rows = [
    row({ user_id: "a", status: "active" }),
    row({ user_id: "b", status: "pending" }),
    row({ user_id: "c", status: "active" }),
  ];
  expect(sectionRows(rows, "pending").map((r) => r.user_id)).toEqual(["b"]);
  expect(sectionRows(rows, "active").map((r) => r.user_id)).toEqual(["a", "c"]);
});

it("核心两区空着也显示，异常两区空了就隐藏", () => {
  const empty: AdminStatsRow[] = [];
  const pending = ADMIN_SECTIONS.find((s) => s.id === "pending")!;
  const banned = ADMIN_SECTIONS.find((s) => s.id === "banned")!;
  expect(showSection(empty, pending)).toBe(true);
  expect(showSection(empty, banned)).toBe(false);
  expect(showSection([row({ status: "banned" })], banned)).toBe(true);
});

it("概览把失败率算出来；没有调用时是 0 而不是 NaN", () => {
  const none = overviewOf([row({ user_id: "a" })]);
  expect(none.calls).toBe(0);
  expect(none.failureRate).toBe(0);
  expect(none.toolCalls).toBe(0);
  expect(none.toolFailureRate).toBe(0);
  expect(none.toolAvgDurationMs).toBeNull();

  const some = overviewOf([
    row({ user_id: "a", calls: 100, failed: 8, total_tokens: 1493085, active_sessions: 3 }),
    row({ user_id: "b", calls: 83, failed: 8, total_tokens: 500, active_sessions: 1, status: "pending" }),
  ]);
  expect(some.accounts).toBe(2);
  expect(some.awaiting).toBe(1);
  expect(some.calls).toBe(183);
  expect(some.failed).toBe(16);
  expect(some.failureRate).toBeCloseTo(16 / 183, 6);
  expect(some.liveSessions).toBe(4);
});

it("工具侧失败率与模型侧**各有分母**，均延迟按调用数加权", () => {
  const some = overviewOf([
    row({ user_id: "a", tool_calls: 3, tool_failed: 1, tool_avg_duration_ms: 300 }),
    row({ user_id: "b", tool_calls: 6, tool_failed: 2, tool_avg_duration_ms: 600 }),
  ]);
  expect(some.toolCalls).toBe(9);
  expect(some.toolFailed).toBe(3);
  expect(some.toolFailureRate).toBeCloseTo(3 / 9, 6);
  // (300×3 + 600×6) / 9 = 500 —— 等权平均会是 450，那是错的
  expect(some.toolAvgDurationMs).toBeCloseTo(500, 6);
});

it("大数缩写：token 到百万级不缩写没法一眼比", () => {
  expect(compactNumber(183)).toBe("183");
  expect(compactNumber(1493085)).toBe("1.49M");
  expect(compactNumber(999)).toBe("999");
  expect(compactNumber(12000)).toBe("12K");
});

it("毫秒到人读：超过 10 秒就该被看见（超时）", () => {
  expect(durationText(null)).toBe("—");
  expect(durationText(9259)).toBe("9.3s");
  expect(durationText(66726)).toBe("1m07s");
  expect(durationText(800)).toBe("800ms");
});

it("最近活跃：没登录过的人单独说，不混进相对时间", () => {
  const now = new Date("2026-10-16T12:00:00+00:00");
  expect(lastSeenText(row({ last_seen_at: null }), now)).toBe("从未登录");
  expect(
    lastSeenText(row({ last_seen_at: "2026-10-16T11:00:00+00:00" }), now),
  ).toBe("1 小时前");
  expect(
    lastSeenText(row({ last_seen_at: "2026-10-16T11:59:30+00:00" }), now),
  ).toBe("刚刚");
});

it("审批信息：有值才显示；种子管理员没有审批记录", () => {
  expect(approvalText(row({ status: "pending" }))).toBeNull();
  expect(approvalText(row({ status: "active", approved_at: null }))).toBe(
    "无审批记录（种子管理员）",
  );
  expect(
    approvalText(
      row({ status: "active", approved_at: "2026-10-16T10:00:00+00:00", approved_by: "root" }),
    ),
  ).toBe("root 于 2026-10-16 审批");
});