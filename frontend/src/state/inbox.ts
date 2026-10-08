/* 灵感箱的视图计算（M12）：箱 = 两个既有投影（线程卡 + 调研候选）的合并视图。
   不存数据、不发请求——数字与列表都从后端真身现算，组件不许自己判断。 */

import type { CardView, ExplorationView, ThreadView } from "../api/types";
import { reviewCandidates } from "./explorations";

/** 箱里的一张卡＋它所属的线程（渲染要显示"来自哪条讨论"）。 */
export interface InboxCard {
  thread: ThreadView;
  card: CardView;
}

/** 还没过目的卡（state=new），按线程顺序、卡顺序。 */
export function inboxCards(threads: ThreadView[]): InboxCard[] {
  return threads.flatMap((thread) =>
    thread.cards
      .filter((card) => card.state === "new")
      .map((card) => ({ thread, card })),
  );
}

/** 未处理数：箱里可动作的条目数（标签徽标的唯一真相）。 */
export function inboxCount(
  threads: ThreadView[],
  explorations: ExplorationView[],
): number {
  const cards = inboxCards(threads).length;
  const candidates = explorations.reduce(
    (sum, item) => sum + reviewCandidates(item).length,
    0,
  );
  return cards + candidates;
}
