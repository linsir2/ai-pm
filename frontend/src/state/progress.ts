/* 本轮执行进度：全部从后端投影的字段推出来，不猜、不编。
   能读的就这几样：step / waiting_on / seat_round_count / claims / questions / draft。 */

import type { RoundView } from "../api/types";
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

/** 右栏要不要展开：有待办的轮次才占 500px（06 §2.4 §3）。 */
export function needsLoopAttention(round: RoundView | null): boolean {
  return round !== null && LOOP_TASKS.includes(round.waiting_on);
}

/** rail 徽标数：未答题数 + 草案待确认组数（0/1）。
    `answer_sets` 的 answered/skipped 是**计数**（`api/types.ts:336`），不是题 id——
    未答 = 当前批题数 − 历批已处理计数（下限 0，不碰 drafts 本地态）。 */
export function loopBadgeCount(round: RoundView | null): number {
  if (round === null) return 0;
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
