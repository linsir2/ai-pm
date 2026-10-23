/* 管理后台的形状层：分区、每行动作、顶部概览的数。
 *
 * **组件一个业务判断都不做**（`App.tsx` 的既有纪律）：这一屏所有"谁该被分到哪一区、
 * 这一行给哪些按钮、概览那几个数怎么算"全在这里，组件只调用 + 渲染。
 *
 * **按状态分区，而不是一张平铺的表**：管理员打开这一屏第一个问题是"有没有人在等我批"，
 * 第二个是"现在谁在用"。平铺的表把这两个问题都埋在同一个滚动列表里。
 * 核心两区（待审批 / 已生效）**永远显示**——空的时候要看得见"没有待批的"本身就是答案；
 * 异常两区（已拒绝 / 封禁中）**空了就隐藏**，不占版面。
 *
 * **每行的动作是服务端状态机的刻意子集，不是镜像**：
 *   - `pending` 给「批准 / 拒绝」——这一行还没生效，封禁它没有意义；
 *   - `active` 给「封禁」；
 *   - `rejected` / `banned` 给「批准」（一键恢复，别让误操作不可逆）；
 *   - **admin 行一个按钮都不给**：它不能被封禁也不能被拒绝（后端 `admin_protected`
 *     硬拦），一个点了必然报错的按钮只会让人以为坏了。后端拦的是底线，界面不给死按钮
 *     是本分。
 */

import type { AdminStatsRow } from "../api/client";

export type AdminSectionId = "pending" | "active" | "rejected" | "banned";

export interface AdminSection {
  id: AdminSectionId;
  title: string;
  /** 空的时候还显不显示。核心两区 true，异常两区 false。 */
  keepWhenEmpty: boolean;
  /** 一句话说清这一区的人现在是什么处境。 */
  note: string;
}

/** 四个区的定义（顺序即页面顺序：先要你动手的，再已经在用的，最后是历史）。 */
export const ADMIN_SECTIONS: readonly AdminSection[] = [
  {
    id: "pending",
    title: "待审批",
    keepWhenEmpty: true,
    note: "注册过、还没放行。放行之后才能登录。",
  },
  {
    id: "active",
    title: "已生效",
    keepWhenEmpty: true,
    note: "能登录、能建项目，看得到且只看得到自己的内容。",
  },
  {
    id: "rejected",
    title: "已拒绝",
    keepWhenEmpty: false,
    note: "被拒过的人可以再放行——误操作不必不可逆。",
  },
  {
    id: "banned",
    title: "封禁中",
    keepWhenEmpty: false,
    note: "封禁会当场吊销该账号全部会话。想放行就从这里恢复。",
  },
] as const;

export type AdminActionKind = "approve" | "reject" | "ban" | "none";

export interface AdminAction {
  kind: AdminActionKind;
  label: string;
}

/** 这一行给哪些动作。没有就给 `none`——**不给一个必然报错的按钮**。 */
export function actionsFor(row: AdminStatsRow): readonly AdminAction[] {
  // admin 直接给空数组——不是给一个 none 再让组件过滤：空就是空。
  if (row.role === "admin") return [];
  if (row.status === "pending") {
    return [
      { kind: "approve", label: "批准" },
      { kind: "reject", label: "拒绝" },
    ];
  }
  if (row.status === "active") return [{ kind: "ban", label: "封禁" }];
  // rejected / banned：一键放行就是"恢复"
  return [{ kind: "approve", label: "恢复" }];
}

/** 按状态分区（保持传入顺序 = 后端给的注册先后）。 */
export function sectionRows(
  rows: readonly AdminStatsRow[],
  section: AdminSectionId,
): AdminStatsRow[] {
  return rows.filter((row) => row.status === section);
}

/** 这一屏要不要画这个区。 */
export function showSection(
  rows: readonly AdminStatsRow[],
  section: AdminSection,
): boolean {
  return section.keepWhenEmpty || sectionRows(rows, section.id).length > 0;
}

/** 概览里要的那几个数。一个都不在组件里算。 */
export interface AdminOverview {
  accounts: number;
  awaiting: number;
  calls: number;
  failed: number;
  /** 失败占比（0–1）。没有调用时给 0——"没调用"不是"0% 失败"。 */
  failureRate: number;
  toolCalls: number;
  toolFailed: number;
  /** 工具侧失败占比（0–1），与 `failureRate` 同一写法、**各自的分母**。 */
  toolFailureRate: number;
  /** 工具均延迟（毫秒，按各行调用数加权）；没有工具调用就是 null。 */
  toolAvgDurationMs: number | null;
  tokens: number;
  liveSessions: number;
}

export function overviewOf(rows: readonly AdminStatsRow[]): AdminOverview {
  const accounts = rows.length;
  let calls = 0;
  let failed = 0;
  let toolCalls = 0;
  let toolFailed = 0;
  let toolDurationSum = 0;
  let toolDurationCount = 0;
  let tokens = 0;
  let liveSessions = 0;
  let awaiting = 0;
  for (const row of rows) {
    calls += row.calls;
    failed += row.failed;
    toolCalls += row.tool_calls;
    toolFailed += row.tool_failed;
    tokens += row.total_tokens;
    liveSessions += row.active_sessions;
    if (row.status === "pending") awaiting += 1;
    // **不能**把每行的均值再等权平均（各行调用数不同，那是错的）——按各行的调用数加权。
    if (row.tool_avg_duration_ms !== null && row.tool_calls > 0) {
      toolDurationSum += row.tool_avg_duration_ms * row.tool_calls;
      toolDurationCount += row.tool_calls;
    }
  }
  return {
    accounts,
    awaiting,
    calls,
    failed,
    toolCalls,
    toolFailed,
    failureRate: calls === 0 ? 0 : failed / calls,
    toolFailureRate: toolCalls === 0 ? 0 : toolFailed / toolCalls,
    toolAvgDurationMs:
      toolDurationCount === 0 ? null : toolDurationSum / toolDurationCount,
    tokens,
    liveSessions,
  };
}

/** `1394K` 这种缩写：token 数到百万级，不缩写没法一眼比。 */
export function compactNumber(value: number): string {
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${Math.round(value / 1000)}K`;
  return `${(value / 1_000_000).toFixed(value < 10_000_000 ? 2 : 1)}M`;
}

/** 毫秒 → 人读：0.8s / 12s / 3m20s。超过 10 秒就该被看见（超时）。 */
export function durationText(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`;
  const minutes = Math.floor(ms / 60_000);
  const rest = Math.round((ms % 60_000) / 1000);
  return `${minutes}m${String(rest).padStart(2, "0")}s`;
}

/**
 * 最近活跃：人读相对时间 + 原始串放在 title 里。
 * **没登录过的人单独说**——「一周前还没来过」和「今天来过」是两个不同的风险画像，
 * 混进同一个相对时间就看不出差别（这是管理后台该说清的事，不是花活）。
 */
export function lastSeenText(row: AdminStatsRow, now: Date): string {
  if (row.last_seen_at === null) return "从未登录";
  const at = new Date(row.last_seen_at);
  if (Number.isNaN(at.getTime())) return "—";
  const minutes = Math.floor((now.getTime() - at.getTime()) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return `${Math.floor(days / 30)} 个月前`;
}

/** 注册于 / 审批于 / 审批人 —— 有值才显示，别拿 "—" 糊一屏。 */
export function approvalText(row: AdminStatsRow): string | null {
  if (row.status === "pending") return null;
  if (row.approved_at === null) return "无审批记录（种子管理员）";
  const at = row.approved_at.slice(0, 10);
  return row.approved_by === null ? `审批于 ${at}` : `${row.approved_by} 于 ${at} 审批`;
}