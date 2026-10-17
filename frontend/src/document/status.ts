/* 章节状态：文档区、章节导航、闭环区共用一套判断，颜色只在这里映射一次。

   优先级 conflict > open > gap > filled > empty：
   有矛盾要裁决最要紧，其次是开放问题，再次是"这一轮有提案等着你确认"，
   然后才算已经有内容。 */

import { sectionIdOf } from "../labels";
import type { ReadinessItemView } from "../api/types";

export type SectionStatus = "empty" | "filled" | "gap" | "open" | "conflict";

/** 只依赖用得到的那一小片：纯函数不假装自己认识整个 DTO。 */
export interface SectionSlice {
  section_id: string;
  blocks: readonly BlockSlice[];
}

/** 判"有内容"看的是**非空条目**：s8 的六个空槽是骨架，不算内容。 */
export interface BlockSlice {
  entries: readonly unknown[];
}

export interface RoundSlice {
  questions?: readonly { kind: string; target_label: string }[];
  draft?: { proposals: readonly { target_label: string }[] } | null;
}

/** 席位包的一小片：`canSkipSeat` 只认这三样，不认识整个 DTO。 */
export interface SeatPacketSlice {
  seat_id: string;
  status: string;
}

/** 用户能不能对这个席位点"跳过"。

三条全满足才亮按钮，少一条都不亮（按钮只做展示，真正的守门在后端）：
1. 轮在跑（`running`：闭环区有在途动作）——停着的轮点跳过没有意义；
2. 包还没交回（`status === "pending"`）——交回了跳不动（后端 409）；
   已跳过的也不再亮（`status === "skipped"`，幂等由后端保证，这里只是不展示）。
3. 在本趟编制里（`activeSeats` 含它）——narrowed 掉的席位本来就不跑，
   对它点跳过只会撞 `UnknownSeat`。
 */
export function canSkipSeat(
  packet: SeatPacketSlice,
  running: boolean,
  activeSeats: readonly string[],
): boolean {
  return (
    running === true &&
    packet.status === "pending" &&
    activeSeats.includes(packet.seat_id)
  );
}

export function sectionStatus(
  section: SectionSlice,
  round: RoundSlice | null,
): SectionStatus {
  const id = section.section_id;
  const questions = round?.questions ?? [];
  const inSection = (target: string) => sectionIdOf(target) === id;

  if (
    questions.some(
      (question) =>
        inSection(question.target_label) &&
        (question.kind === "conflict" || question.kind === "decision"),
    )
  ) {
    return "conflict";
  }
  if (questions.some((question) => inSection(question.target_label) && question.kind === "gap")) {
    return "open";
  }
  const proposals = round?.draft?.proposals ?? [];
  if (proposals.some((proposal) => inSection(proposal.target_label))) return "gap";
  if (section.blocks.some((block) => block.entries.length > 0)) return "filled";
  return "empty";
}

export function statusClass(status: SectionStatus): string {
  return `dot dot--${status}`;
}

export interface SectionBadge {
  block: number;
  warn: number;
}

/** 缺口戳计数：severity 原样分桶（DESIGN.md 状态色一套，计数不动语义）。 */
export function sectionBadge(
  sectionId: string,
  readiness: readonly ReadinessItemView[],
): SectionBadge | null {
  let block = 0;
  let warn = 0;
  for (const item of readiness) {
    if (item.section_id !== sectionId) continue;
    if (item.severity === "blocker") block += 1;
    else warn += 1;
  }
  return block === 0 && warn === 0 ? null : { block, warn };
}

/** 状态在界面上的人话。颜色与词都在这一处对齐 DESIGN.md 的四个语义色。 */
export const STATUS_TEXT: Record<SectionStatus, string> = {
  empty: "空",
  filled: "有内容",
  gap: "本轮有提案待确认",
  open: "有开放问题",
  conflict: "有矛盾待裁决",
};

/** 有内容的章数：版本条上那个"n 章有内容"。空与有内容的分界就是"有没有块"。 */
export function filledSectionCount(document: { sections: readonly SectionSlice[] } | null): number {
  if (document === null) return 0;
  return document.sections.filter(
    (section) => sectionStatus(section, null) !== "empty",
  ).length;
}
