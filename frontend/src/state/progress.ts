/* 本轮执行进度：全部从后端投影的字段推出来，不猜、不编。
   能读的就这几样：step / waiting_on / seat_round_count / claims / questions / draft。 */

import type { LimitsView, RoundView } from "../api/types";
import { limitsOf } from "./schema";

export type ProgressState = "done" | "active" | "todo";

export interface ProgressStep {
  key: string;
  text: string;
  state: ProgressState;
  detail: string | null;
}

export function roundProgress(
  round: RoundView | null,
  limits: LimitsView | null = null,
): ProgressStep[] {
  if (round === null) return [];

  // 席位轮上限的真相源在后端（`GET /schema`）；调用方没给就用已经存下的那一份。
  const maxSeatRounds = (limits ?? limitsOf()).max_seat_rounds;
  const done = round.step === "done";
  const openingDone = round.seats.length > 0;
  const seatsDone = round.seat_packets.length > 0 && round.seat_packets.every(isCompleted);
  // 席位轮只有出了草案（或整轮结束）才算 done；出了问卷不等于完成（答完可能再跑一轮）
  const seatsFullyDone = seatsDone && (round.draft !== null || done);
  const integrated = round.questions.length > 0 || round.draft !== null;

  return [
    {
      key: "claims",
      text: "主张",
      state: round.claims.length > 0 ? "done" : "active",
      detail: round.claims.length > 0 ? `${round.claims.length} 条` : null,
    },
    {
      key: "opening",
      text: "复述",
      state: openingDone ? "done" : round.waiting_on === "opening_card" ? "active" : "todo",
      detail: openingDone ? `${round.seats.length} 个席位` : null,
    },
    {
      key: "seats",
      text: "席位轮",
      state: seatsFullyDone ? "done" : openingDone ? "active" : "todo",
      detail: `${Math.min(round.seat_round_count, maxSeatRounds)} / ${maxSeatRounds} 轮`,
    },
    {
      key: "integration",
      text: "整合",
      state: integrated || done ? "done" : seatsDone ? "active" : "todo",
      detail:
        round.draft === null
          ? round.questions.length > 0
            ? `${round.questions.length} 题`
            : null
          : `${round.draft.proposals.length} 条候选`,
    },
    {
      key: "questionnaire",
      text: "问卷",
      state:
        round.waiting_on === "questionnaire"
          ? "active"
          : round.questions.length > 0
            ? "done"
            : "todo",
      detail: round.questions.length > 0 ? `${round.questions.length} 题` : "没有要问的",
    },
    {
      key: "draft",
      text: "草案",
      state:
        round.draft === null
          ? "todo"
          : round.waiting_on === "draft_card"
            ? "active"
            : "done",
      detail: round.draft === null ? null : `${round.draft.proposals.length} 条`,
    },
    {
      key: "confirm",
      text: "确认",
      state: done ? "done" : round.step === "awaiting_confirm" ? "active" : "todo",
      detail: round.confirmed_version_no === null ? null : `v${round.confirmed_version_no}`,
    },
  ];
}

function isCompleted(packet: { status: string }): boolean {
  return packet.status === "completed";
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
