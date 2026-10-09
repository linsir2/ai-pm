/* 就绪度的界面口径：把后端那份判据清单变成三个纯函数能回答的问题——
   一共差几项、这一章差什么、版本条上写什么。

   这里不做判断，只做归拢：判据的真相源在后端 `domain/authoring/readiness.py`。 */

import type { ReadinessItemView } from "../api/types";

export interface ReadinessSummary {
  blockers: number;
  warns: number;
  bySection: Map<string, ReadinessItemView[]>;
}

export function readinessSummary(items: readonly ReadinessItemView[]): ReadinessSummary {
  const bySection = new Map<string, ReadinessItemView[]>();
  let blockers = 0;
  let warns = 0;
  for (const item of items) {
    if (item.severity === "blocker") blockers += 1;
    else warns += 1;
    const bucket = bySection.get(item.section_id);
    if (bucket === undefined) bySection.set(item.section_id, [item]);
    else bucket.push(item);
  }
  return { blockers, warns, bySection };
}

export function readinessItemsFor(
  items: readonly ReadinessItemView[],
  sectionId: string,
): ReadinessItemView[] {
  return items.filter((item) => item.section_id === sectionId);
}

/** 版本条上那一小段：有阻塞说"就差 N 项"，只有提示说"提示 N 项"，都没有就说"就绪"。 */
export function readinessHeadline(items: readonly ReadinessItemView[]): string {
  const { blockers, warns } = readinessSummary(items);
  if (blockers > 0) return `就差 ${blockers} 项`;
  if (warns > 0) return `提示 ${warns} 项`;
  return "就绪";
}

/**
 * 就绪度百分比：**无阻塞章节 / 参与判定的章节**。
 *
 * s13 不参与（与后端判据一致：版本历史不是人写的正文）；
 * 左栏环形图与文档区「就绪度 N%」用同一个算法，这里算一次两处用。
 */
export function readinessPercent(
  items: readonly ReadinessItemView[],
  sections: readonly { section_id: string }[],
): number {
  const { bySection } = readinessSummary(items);
  const graded = sections.filter((section) => section.section_id !== "s13");
  if (graded.length === 0) return 0;
  const ready = graded.filter((section) =>
    (bySection.get(section.section_id) ?? []).every((item) => item.severity !== "blocker"),
  ).length;
  return Math.round((ready / graded.length) * 100);
}

export interface SectionReadiness {
  sectionId: string;
  title: string;
  items: ReadinessItemView[];
  blockers: number;
}

/**
 * 「就差 N 项」点开之后那份清单：**按章分组**（`06-前端.md` §2.2 的承诺，P29 F6 补上）。
 *
 * 只保留真有项的章节，章节顺序跟传入的 sections 走（不重排，免得和左栏对不上）。
 */
export function readinessBySection(
  items: readonly ReadinessItemView[],
  sections: readonly { section_id: string; title: string }[],
): SectionReadiness[] {
  const { bySection } = readinessSummary(items);
  return sections
    .map((section) => {
      const list = bySection.get(section.section_id) ?? [];
      return {
        sectionId: section.section_id,
        title: section.title,
        items: list,
        blockers: list.filter((item) => item.severity === "blocker").length,
      };
    })
    .filter((group) => group.items.length > 0);
}
