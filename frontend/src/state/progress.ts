/* 本轮执行进度：全部从后端投影的字段推出来，不猜、不编。
   能读的就这几样：step / waiting_on / seat_round_count / claims / questions / draft。 */

import type { RoundView, WebRequestView } from "../api/types";
import { stepText } from "../labels";

/** 「轮到你」的那几件事（后端 `waiting_on` 的枚举）。`none` 不在其中——
    "没人需要动手"是设计语言，不到用户眼前。 */
const USER_WAIT_TEXT: Record<string, string> = {
  opening_card: "等你在复述卡上纠正",
  questionnaire: "等你交问卷",
  draft_card: "等你勾草案",
};

/** 状态行那一句：**waiting 优先于 step**（等你是最该说的事），一步只说一句——
    "等你回答 · 等你交问卷"这种叠句不再出现。 */
export function statusText(round: RoundView): string {
  if (round.step === "done") return "这一轮写完了";
  if (round.step === "failed") return "这一轮失败了";
  // 放弃由失败行说「已放弃：<原因>」，这里不再叠一句
  if (round.step === "abandoned") return "";
  // 席位在跑时不报 waiting（此刻的 waiting_on 多是 none，报"等谁"会误导）
  if (round.step !== "seats_running") {
    const wait = USER_WAIT_TEXT[round.waiting_on];
    if (wait !== undefined) return wait;
  }
  return stepText(round.step);
}

const LOOP_TASKS: readonly string[] = ["opening_card", "questionnaire", "draft_card"];

/** 右栏要不要展开：有待办的轮次才占 500px（06 §2.4 §3）。
    席位在跑也算有待办：那三条进 `seats_running` 的路（开场确认 / 交卷 / 深挖）
    全是 `waiting_on=none`（`round.py` 的 `_advance_to` 默认值），waiting 判不动，只能看 step。 */
export function needsLoopAttention(round: RoundView | null): boolean {
  if (round === null) return false;
  if (round.step === "seats_running") return true;
  return LOOP_TASKS.includes(round.waiting_on);
}

/** rail 徽标数：未答题数 + 草案待确认组数（0/1）。
    非任务态（`needsLoopAttention` 为 false，含 done 轮的 draft 残留）一律归零——
    徽标只给"现在要点开 rail 去干活"计数，不给 stale 投影计数。
    `answer_sets` 的 answered/skipped 是**计数**（`api/types.ts:336`），不是题 id——
    未答 = 当前批题数 − 历批已处理计数（下限 0，不碰 drafts 本地态）。 */
export function loopBadgeCount(round: RoundView | null): number {
  if (round === null || !needsLoopAttention(round)) return 0;
  let done = 0;
  for (const batch of round.answer_sets ?? []) {
    done += batch.answered + batch.skipped;
  }
  const open = Math.max(0, (round.questions ?? []).length - done);
  return open + (round.draft === null || round.draft === undefined ? 0 : 1);
}

/**
 * 第几轮（展示序号）：`rounds` 最新在前，序号 = 总数 − 位置。
 * 查不到（列表还没到 / 不在列表里）就是 null——界面回落成"本轮"，不编一个数。
 */
export function roundOrdinal(
  rounds: readonly { round_id: string }[],
  roundId: string | null,
): number | null {
  if (roundId === null) return null;
  const index = rounds.findIndex((item) => item.round_id === roundId);
  if (index < 0) return null;
  return rounds.length - index;
}

/** 这个轮从开局到现在多久（"开局 X 分钟"）：拿 rounds 列表里同 round_id 的 created_at 现算。
    找不到 / 时间在未来就是 null——界面回落成不显示，不编一个数。
    **口径**：这是时间年龄（含等人时间），不是"有效运行时长"——后端没存每步时间戳，
    写"已跑"就是撒谎（真跑 19 分钟里大半在等用户）。 */
export function roundElapsed(
  rounds: readonly { round_id: string; created_at: string }[],
  roundId: string | null,
  now: number,
): string | null {
  if (roundId === null) return null;
  const head = rounds.find((item) => item.round_id === roundId);
  if (head === undefined) return null;
  const ms = now - Date.parse(head.created_at);
  if (Number.isNaN(ms) || ms < 0) return null;
  const minutes = Math.floor(ms / 60000);
  if (minutes < 1) return "刚刚开局";
  if (minutes < 60) return `开局 ${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `开局 ${hours} 小时`;
  return `开局 ${Math.floor(hours / 24)} 天`;
}

/** 还没走完的联网申请（proposed / approved）：徽标只报需要留意的。 */
export function openWebRequests(round: RoundView | null): WebRequestView[] {
  return (round?.web_requests ?? []).filter(
    (item) => item.state === "proposed" || item.state === "approved",
  );
}

/** 已占用的联网执行名额：approved / done / failed 都算（proposed / rejected 不占）。 */
export function executedWebRequestCount(round: RoundView | null): number {
  return (round?.web_requests ?? []).filter(
    (item) =>
      item.state === "approved" || item.state === "done" || item.state === "failed",
  ).length;
}

export interface WebExecutionSlots {
  used: number;
  selected: number;
  /** 剩余可批准/勾选的条数；limit 没读到时是 null（不猜）。 */
  remaining: number | null;
}

/** 开场卡显示“剩余 N/M”：已用（approved/done/failed）+ 已勾（本地草稿）。 */
export function webExecutionSlots(
  used: number,
  selected: number,
  limit: number | null,
): WebExecutionSlots {
  return {
    used,
    selected,
    remaining: limit === null ? null : Math.max(0, limit - used - selected),
  };
}

/** 闭环区那一行徽标：0 条不渲染（返回 null）。 */
export function webRequestBadge(round: RoundView | null): string | null {
  const count = openWebRequests(round).length;
  return count === 0 ? null : `有 ${count} 条联网申请`;
}

/** 主张的标签分布（分析者那一格要显示的"3 种标签"）。 */
export function labelCounts(round: RoundView): [string, number][] {
  const counts = new Map<string, number>();
  for (const claim of round.claims) {
    counts.set(claim.label, (counts.get(claim.label) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]);
}

/** 草案里"改判"几条：op 不是 add 的那些（replace / remove）。 */
export function rewritesOf(draft: { proposals: { op: string }[] } | null): number {
  if (draft === null) return 0;
  return draft.proposals.filter((proposal) => proposal.op !== "add").length;
}
