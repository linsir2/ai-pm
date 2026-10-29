/* 决策卡弹出状态机（2026-10-29 主闭环对话流）：纯函数，组件只负责画。
   规矩只有一条：**`waiting_on` 是唯一真相源**——该不该弹、弹哪张，都从这一轮的
   投影推出来；"用户收起了"是唯一能压住它的本地状态，且同一 `waiting_on` 不再强弹
   （header 的 chip 随时可以重开）。 */

import type { RoundView } from "../api/types";

export type DrawerKind = "opening" | "questionnaire" | "draft";

/** 这一轮现在等着决策的是哪张卡（与 `waiting_on` 一一对应）。 */
export function pendingDrawer(round: RoundView | null): DrawerKind | null {
  if (round === null) return null;
  switch (round.waiting_on) {
    case "opening_card":
      return "opening";
    case "questionnaire":
      return (round.questions?.length ?? 0) > 0 ? "questionnaire" : null;
    case "draft_card":
      return (round.draft?.proposals.length ?? 0) > 0 ? "draft" : null;
    default:
      return null;
  }
}

/** 现在真的要弹的吗：`waiting_on` 说了算，用户收起（collapsed）能压住。 */
export function drawerFor(
  round: RoundView | null,
  collapsed: boolean,
): DrawerKind | null {
  if (collapsed) return null;
  return pendingDrawer(round);
}

/** 收起之后 header 上的那枚"还差一张卡"chip（没有待办就是 null）。 */
export function drawerChip(
  kind: DrawerKind,
  round: RoundView,
): { label: string; detail: string } | null {
  switch (kind) {
    case "opening":
      return { label: "复述卡", detail: "待你提交" };
    case "questionnaire":
      return { label: "问卷", detail: `${round.questions.length} 题待答` };
    case "draft":
      return {
        label: "草案",
        detail: `${round.draft?.proposals.length ?? 0} 条候选`,
      };
    default:
      return null;
  }
}
