/* 过目台的判断（M8）：纯函数。组件只负责把这里的结果画出来。
   数据一律来自后端的 `ExplorationView` 投影，这里不拼、不猜。 */

import type { CandidateView, ExplorationView } from "../api/types";

/** 候选卡上的动作（与后端 keep/dismiss 两个端点一一对应）。 */
export type CandidateAction = "keep-seed" | "keep-material" | "dismiss";

/** 还有东西要过目的探索：有 `new` 候选的那些（进来的顺序已是后端的最新在前）。 */
export function trayExplorations(list: ExplorationView[]): ExplorationView[] {
  return list.filter((item) => item.candidates.some((candidate) => candidate.state === "new"));
}

/** 还没过目的候选（按后端给的顺序）。 */
export function trayCandidates(exploration: ExplorationView): CandidateView[] {
  return exploration.candidates.filter((candidate) => candidate.state === "new");
}

/** 这条候选现在能做什么：只有 `new` 三条路都通；收下/删掉之后后端也会拒，界面先不画按钮。 */
export function candidateActions(candidate: CandidateView): CandidateAction[] {
  return candidate.state === "new" ? ["keep-seed", "keep-material", "dismiss"] : [];
}

/**
 * 收下为「下一轮输入」时往输入框里写的话。
 *
 * 假设用原文；盲区没有现成措辞，拼一句"需要搞清楚"，有下一步就带上——
 * 只用户给的信息，不替模型补事实。
 */
export function seedText(candidate: CandidateView): string {
  if (candidate.kind !== "blind_spot") return candidate.statement;
  const step = candidate.next_step?.trim();
  return step
    ? `需要搞清楚：${candidate.statement}（${step}）`
    : `需要搞清楚：${candidate.statement}`;
}

/** 同项目同时只允许一趟探索在跑（后端 409）——据此禁用"发起调研"。 */
export function isRunning(list: ExplorationView[]): boolean {
  return list.some((item) => item.status === "running");
}

/** 失败卡上那句话：有原因照实说，没原因也不留空。 */
export function failureText(exploration: ExplorationView): string {
  const reason = exploration.failure_reason?.trim();
  return reason ? reason : "这趟调研失败了（后端没留下原因）";
}

/** 超上限的提示（没超就什么都不说）。 */
export function overflowNote(exploration: ExplorationView): string | null {
  return exploration.overflow_count > 0
    ? `超出上限，已截断 ${exploration.overflow_count} 条`
    : null;
}
