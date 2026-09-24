/* 章节状态：文档区、章节导航、闭环区共用一套判断，颜色只在这里映射一次。

   优先级 conflict > open > gap > filled > empty：
   有矛盾要裁决最要紧，其次是开放问题，再次是"这一轮有提案等着你确认"，
   然后才算已经有内容。 */

import { sectionIdOf } from "../labels";

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
