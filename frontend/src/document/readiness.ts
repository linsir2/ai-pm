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
