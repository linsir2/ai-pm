/* `/schema` 的落地位置：**章节说明 / 字段中文名 / 上限常量**的真相源在后端，
   前端不再各自抄一份（`06-前端.md` §5 记的那两处重复，以及 `LoopPane` 里那第三份）。

   两件事：
   1. `setSchema()`——拉到之后存一份，供渲染期间要在纯函数里查词的组件用
      （`document/structure.ts`、`FeatureBlock`、`SideRail` 这些只接收 block 的地方）；
   2. 查不到就回落 `FALLBACK_FIELD_TITLES` / `FALLBACK_LIMITS`——**界面不该因为
      schema 还没到就空着**；回落值只是"后端不变时的同一份值"，不是第二个真相源。

   注意：这里存的不是业务状态（不是文档、不是轮次），只是一张只读对照表。 */

import type { LimitsView, SchemaView } from "../api/types";

/** 功能子树字段的中文名（离线回落；真相源是后端 `domain/authoring/schema.py`）。 */
export const FALLBACK_FIELD_TITLES: Record<string, string> = {
  name: "名称",
  one_liner: "一句话描述",
  priority: "优先级",
  goal: "服务的目标",
  audience: "面向角色",
  user_story: "用户故事",
  main_flow: "主流程",
  exceptions: "异常与边界",
  io: "输入与输出",
  rules: "状态与规则",
  acceptance: "验收标准",
  dependencies: "依赖",
};

/** 必填字段（离线回落；与后端 `REQUIRED_FEATURE_FIELDS` 同一份口径）。 */
export const FALLBACK_REQUIRED_FIELDS: ReadonlySet<string> = new Set([
  "name",
  "one_liner",
  "goal",
  "audience",
  "exceptions",
  "acceptance",
]);

export const FALLBACK_LIMITS: LimitsView = {
  max_seat_rounds: 3,
  max_questionnaires: 2,
  max_open_items_per_batch: 3,
  max_sim_review_personas: 3,
  max_sim_review_features: 2,
  max_discussion_turns: 12,
};

let current: SchemaView | null = null;

export function setSchema(view: SchemaView | null): void {
  current = view;
}

export function currentSchema(): SchemaView | null {
  return current;
}

export function limitsOf(schema: SchemaView | null = current): LimitsView {
  return schema?.limits ?? FALLBACK_LIMITS;
}

export function fieldTitle(
  field: string | null,
  schema: SchemaView | null = current,
): string | null {
  if (field === null) return null;
  return (
    schema?.feature_fields.find((item) => item.name === field)?.title ??
    FALLBACK_FIELD_TITLES[field] ??
    field
  );
}

/** 12 个功能字段（顺序就是后端字段表的顺序）：建"还不存在的字段块"时用。 */
export function featureFields(
  schema: SchemaView | null = current,
): { name: string; title: string; required: boolean }[] {
  if (schema !== null) {
    return schema.feature_fields.map((item) => ({
      name: item.name,
      title: item.title,
      required: item.required,
    }));
  }
  return Object.entries(FALLBACK_FIELD_TITLES).map(([name, title]) => ({
    name,
    title,
    required: FALLBACK_REQUIRED_FIELDS.has(name),
  }));
}

/** 这一章是干什么的（拿不到 schema 就是 null：宁可不显示，也不编一句）。 */
export function sectionPurpose(
  sectionId: string,
  schema: SchemaView | null = current,
): string | null {
  return schema?.sections.find((item) => item.section_id === sectionId)?.purpose ?? null;
}

/** 章节槽的中文名（如 s8.performance → 性能）；认不出返回 null，由调用方回落原值。 */
export function slotTitle(
  sectionId: string,
  field: string,
  schema: SchemaView | null = current,
): string | null {
  const section = schema?.sections.find((item) => item.section_id === sectionId);
  return section?.slots.find((slot) => slot.name === field)?.title ?? null;
}
