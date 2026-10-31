/* 在途动作的人话。界面只认这里的结果，不在组件里堆 if-else 文案。 */

export type PendingAction =
  | "start"
  | "opening"
  | "answers"
  | "confirm"
  | "retry"
  | "dig"
  | "edit"
  /** 材料库上的动作（删 / 重解析）：与"这一轮"无关，但仍然串行，不并发点。 */
  | "material"
  /** 搜索框「带进本轮」：轮内动作，串行不并发。 */
  | "round_material"
  /** 记忆抽屉上的动作（M6）：删一条记忆——同样串行，不与"这一轮"的动作并发。 */
  | "memory"
  /** 过目台上的动作（M8）：发起 / 重试 / 放弃 / 收下 / 删掉，串行不并发。 */
  | "exploration"
  /** 研究来源挂当前轮（P3b-1）：轮内动作，与其它轮内动作一样串行。 */
  | "research"
  /** 讨论区上的动作（M9）：开聊 / 说 / 总结 / 收卡 / 关掉，同样串行不并发。 */
  | "discussion"
  /** 模拟抽屉上的动作（M10）：跑一趟 / 收下勾选，同样串行不并发。 */
  | "simulation"
  /** 模拟评测上的动作（M11）：跑一趟——同样串行不并发。 */
  | "sim_review"
  /** 联网申请逐条决策（P3a）：批准即执行，串行不并发。 */
  | "web_request";

const LABELS: Record<PendingAction, string> = {
  start: "正在起轮…",
  opening: "正在提交开场…",
  answers: "正在提交回答…",
  confirm: "正在确认写入…",
  retry: "正在重试…",
  dig: "正在深挖…",
  edit: "正在写入…",
  material: "正在处理材料…",
  round_material: "正在把材料带进本轮…",
  memory: "正在更新记忆…",
  exploration: "正在调研…",
  research: "正在挂载研究来源…",
  discussion: "正在讨论…",
  simulation: "正在跑模拟调研…",
  sim_review: "正在跑模拟评测…",
  web_request: "正在处理联网申请…",
};

/** 在途动作的界面说法；未知值保守成「正在处理…」。 */
export function pendingActionLabel(action: PendingAction | string | null | undefined): string {
  if (action === null || action === undefined) return "正在处理…";
  return LABELS[action as PendingAction] ?? "正在处理…";
}

/** 动作被串行闸拦下时给用户的话。 */
export function pendingBlockedMessage(action: PendingAction | string | null | undefined): string {
  const label = pendingActionLabel(action).replace(/…$/, "");
  return `当前${label}，完成后再试`;
}
