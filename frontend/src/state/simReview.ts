/* 模拟评测（M11）的纯函数层：默认勾选、可选性、上限回落。

   组件不做业务判断（硬规矩 ②）：选择器的默认值与"能不能开始"全在这里，
   组件只负责把结果画出来、把点击转成回调。

   上限的真相源在后端（`application/sim_review.py` 三个常量经 `/schema` 下发）；
   这里的 3/2 只是"拿不到 schema 时与后端不变时的同一份值"的回落，
   与 `state/schema.ts` 的 `FALLBACK_LIMITS` 同一条纪律。 */

import type {
  DocumentView,
  LimitsView,
  PersonaSchemaView,
} from "../api/types";

export interface SimReviewLimits {
  personas: number;
  features: number;
}

export const FALLBACK_SIM_REVIEW_LIMITS: SimReviewLimits = { personas: 3, features: 2 };

export function reviewLimits(limits: LimitsView | null | undefined): SimReviewLimits {
  return {
    personas: limits?.max_sim_review_personas ?? FALLBACK_SIM_REVIEW_LIMITS.personas,
    features: limits?.max_sim_review_features ?? FALLBACK_SIM_REVIEW_LIMITS.features,
  };
}

/** 默认勾前 N 个（personas.toml 的顺序就是"默认三个 + 可补两个"）。 */
export function defaultPersonaIds(
  personas: PersonaSchemaView[],
  limits: SimReviewLimits,
): string[] {
  return personas.slice(0, limits.personas).map((item) => item.persona_id);
}

/** 文档里的功能序（上到下）：选择器的功能清单。 */
export function documentFeatures(document: DocumentView | null): string[] {
  const slugs: string[] = [];
  for (const section of document?.sections ?? []) {
    for (const block of section.blocks) {
      collect(slugs, block);
    }
  }
  return slugs;
}

function collect(slugs: string[], block: { feature_slug: string | null; children: unknown[] }): void {
  if (block.feature_slug !== null && !slugs.includes(block.feature_slug)) {
    slugs.push(block.feature_slug);
  }
  for (const child of block.children as { feature_slug: string | null; children: unknown[] }[]) {
    collect(slugs, child);
  }
}

/** 默认勾文档前 N 个功能（用户可改；不做"提议触及"的猜测——那要读提案落点，不值）。 */
export function defaultFeatureSlugs(
  document: DocumentView | null,
  limits: SimReviewLimits,
): string[] {
  return documentFeatures(document).slice(0, limits.features);
}

export function canStartReview(
  personaIds: string[],
  featureSlugs: string[],
  limits: SimReviewLimits,
): boolean {
  return (
    personaIds.length >= 1 &&
    personaIds.length <= limits.personas &&
    featureSlugs.length >= 1 &&
    featureSlugs.length <= limits.features
  );
}
