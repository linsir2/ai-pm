/* 项目级研究（P3b-1）的视图计算：纯函数。
   组件只负责把这里的结果画出来——能不能开、能不能挂、按钮为什么灰着，全在这里判。 */

import type { ExplorationView, ResearchSourceView, RoundView } from "../api/types";

/** 只挑研究（`web_research`）；M8 调研（`inquiry`）归过目台，两处不许混。 */
export function researchRuns(list: ExplorationView[]): ExplorationView[] {
  return list.filter((item) => item.kind === "web_research");
}

/** 只挑 M8 调研；过目台与徽标数只认它（研究没有候选）。 */
export function inquiryRuns(list: ExplorationView[]): ExplorationView[] {
  return list.filter((item) => item.kind !== "web_research");
}

/** 同项目同时只允许一趟研究在跑（后端 409）：据此禁用「开始研究」。 */
export function canStartResearch(list: ExplorationView[], pending: boolean): boolean {
  return !pending && !list.some((item) => item.status === "running");
}

/**
 * 主题的长度/空值判据（服务端也判，这里只让按钮先灰）。
 *
 * 上限来自 `GET /schema.limits.max_research_topic_chars`——**不抄数字**：
 * 拿不到（老后端）就不拦长度，交给后端 422。
 */
export function researchTopicError(topic: string, limit?: number): string | null {
  const trimmed = topic.trim();
  if (!trimmed) return "写清要研究什么";
  if (limit !== undefined && trimmed.length > limit) {
    return `最多 ${limit} 字`;
  }
  return null;
}

/** 发起研究时发给后端的东西（只给主题，别的都不猜）。 */
export function buildResearchBody(topic: string): { topic: string } {
  return { topic: topic.trim() };
}

/** 一趟研究的状态那句话（失败要带原因，没原因也不留空）。 */
export function researchStatusText(item: ExplorationView): string {
  switch (item.status) {
    case "running":
      return "研究进行中…";
    case "done": {
      const sources = item.research?.sources ?? [];
      return sources.length > 0 ? `完成：${sources.length} 条来源` : "完成：没有取到来源";
    }
    case "abandoned":
      return `已放弃${item.abandon_reason ? `：${item.abandon_reason}` : ""}`;
    default: {
      const reason = item.failure_reason?.trim();
      return reason ? `失败：${reason}` : "失败（后端没留下原因）";
    }
  }
}

/** 来源卡上那个按钮的三种状态。 */
export type SourceAction = "attach" | "attached" | "blocked";

/**
 * 这张来源卡现在能干什么。
 *
 * - `attached`：这一轮已经挂过它（按 eid 判，后端也幂等）；
 * - `blocked`：没有可挂的轮次，或这一轮已经出了草案（后端 409）——
 *   原因由 `sourceActionReason` 说给用户听。
 */
export function sourceAction(
  source: ResearchSourceView,
  round: RoundView | null,
): SourceAction {
  if (round === null) return "blocked";
  if ((round.research_sources ?? []).some((item) => item.eid === source.eid)) {
    return "attached";
  }
  const attachable = ["started", "claims_ready", "seats_running", "awaiting_answers"];
  return attachable.includes(round.step) ? "attach" : "blocked";
}

/** `blocked` 的原因那句话（能挂时不返回）。 */
export function sourceActionReason(
  action: SourceAction,
  round: RoundView | null,
): string | null {
  if (action !== "blocked") return null;
  if (round === null) return "先开一轮，再挂来源";
  return "这一轮已经出了草案，等下一轮再挂";
}

/** 这一趟研究有没有给出答案（旧报告没有 `answer`，要能说清"只找到来源"）。 */
export function hasAnswer(item: ExplorationView): boolean {
  return (item.research?.answer ?? "").trim().length > 0;
}
