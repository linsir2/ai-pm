/* 后端只给枚举值，中文词只在这里映射一次。
   加一个映射之前先问：后端有没有这个值？没有就别在前端发明。 */

import type { SectionView } from "./api/types";
import { fieldTitle, slotTitle } from "./state/schema";

/** 模板章节角色（`02` §11.0.70）。真相源是后端 `TemplateRole`——这里只映中文词，
 *  可选值本身问 `state/schema.ts` 的 `templateRoles()`（前端不抄一份枚举）。 */
export const ROLE_TEXT: Record<string, string> = {
  feature: "功能需求",
  open_questions: "风险与开放问题",
  version_history: "版本历史",
  steps: "流程步骤",
  decision: "关键决策",
  data: "数据与接口",
  backbone: "骨架章",
  plain: "普通内容章",
};

export const CLAIM_LABEL_TEXT: Record<string, string> = {
  fact: "事实",
  need: "需求",
  goal: "目标",
  constraint: "约束",
  preference: "偏好",
  guess: "猜测",
  decision: "决策",
};

export const CLAIM_LABEL_OPTIONS = Object.entries(CLAIM_LABEL_TEXT).map(([value, text]) => ({
  value,
  text,
}));

export function claimLabelText(label: string): string {
  return CLAIM_LABEL_TEXT[label] ?? label;
}

export function claimLabelClass(label: string): string {
  return `tag tag--${label}`;
}

export const QUESTION_KIND_TEXT: Record<string, string> = {
  gap: "缺口",
  conflict: "矛盾",
  decision: "决策",
};

export const QUESTION_TYPE_TEXT: Record<string, string> = {
  single: "单选",
  multi: "多选",
  text: "问答",
};

export const STEP_TEXT: Record<string, string> = {
  started: "刚开始",
  claims_ready: "已经复述完你的话",
  seats_running: "席位在跑",
  awaiting_answers: "等你回答",
  awaiting_confirm: "等你确认草案",
  done: "这一轮写完了",
  failed: "这一轮失败了",
  abandoned: "这一轮已放弃",
};

export const ORIGIN_TEXT: Record<string, string> = {
  user: "你说过的",
  derived: "它替你判断的",
  proposed: "它提议的",
  system_default: "系统按规则补的",
};

export const OP_TEXT: Record<string, string> = {
  add: "新增",
  replace: "改写",
  remove: "删除",
};

export const OPINION_FORM_TEXT: Record<string, string> = {
  judgment: "判断",
  question: "疑问",
};

export const SEAT_STATE_TEXT: Record<string, string> = {
  completed: "已完成",
  pending: "待启动",
};

export const EXPLORATION_STATUS_TEXT: Record<string, string> = {
  running: "进行中",
  done: "已完成",
  failed: "失败",
  abandoned: "已放弃",
};

export const CANDIDATE_KIND_TEXT: Record<string, string> = {
  hypothesis: "假设",
  blind_spot: "盲区",
};

/** 过目动作的词（调研候选与讨论卡共用一套——同一箱里同一动作只有一个说法）。 */
export const REVIEW_ACTION_TEXT: Record<string, string> = {
  "keep-seed": "收下为下一轮输入",
  "keep-material": "收下为材料",
  dismiss: "忽略",
};

export function stepText(step: string): string {
  return STEP_TEXT[step] ?? step;
}

export function opinionFormText(form: string): string {
  return OPINION_FORM_TEXT[form] ?? form;
}

/** `Target.label()` 的形状表（与后端 `target_from_label` 同构；形状不认返回 null，不猜）。

    五种合法形态：`preference` / `s5` / `s8.performance`（章节槽，**无斜杠**）/
    `s5/slug` / `s5/slug.field`。残缺（`s5/`、`s5/x.`、`.` 开头、空串）一律 null。 */
export type ParsedTarget =
  | { zone: "preference" }
  | { zone: "body"; sectionId: string; slug: string | null; field: string | null };

export function parseTarget(label: string): ParsedTarget | null {
  const text = label.trim();
  if (text === "") return null;
  if (text === "preference") return { zone: "preference" };
  const [head, dot, field] = splitTriple(text, ".");
  if (dot !== "" && field === "") return null; // `s5/x.` 写了点没写字段名
  const [sectionId, slash, slug] = splitTriple(head, "/");
  if (slash !== "" && slug === "") return null; // `s5/` 写了斜杠没写功能名
  if (sectionId === "") return null; // `.performance` 这种没有章名
  return { zone: "body", sectionId, slug: slash === "" ? null : slug, field: dot === "" ? null : field };
}

/** `s5` / `s5/feat-slug` / `s5/feat-slug.acceptance` / `s8.performance` / `preference` → 人话。 */
export function targetText(target: string, sections?: SectionView[]): string {
  const parsed = parseTarget(target);
  if (parsed === null) return target; // 形状不认：照实显示原值，不编
  if (parsed.zone === "preference") return "偏好区";
  const title = sectionTitle(parsed.sectionId, sections);
  const fieldText =
    parsed.field === null
      ? null
      : parsed.slug === null
        ? (slotTitle(parsed.sectionId, parsed.field) ?? parsed.field)
        : (fieldTitle(parsed.field) ?? parsed.field);
  if (parsed.slug !== null) {
    return fieldText === null ? `${title} · ${parsed.slug}` : `${title} · ${parsed.slug} · ${fieldText}`;
  }
  return fieldText === null ? title : `${title} · ${fieldText}`;
}

/** `s5` / `s5/feat-slug` / `s5/feat-slug.acceptance` / `preference` → `s5` / `preference`。 */
export function sectionIdOf(target: string): string {
  const at = target.indexOf("/");
  return at < 0 ? target : target.slice(0, at);
}

/** 闭环区里挤不下全称时用的短写法：`s5 · 验收标准`。不带功能名，功能名在别处已有。 */
export function compactTarget(target: string): string {
  if (target === "preference") return "偏好区";
  const [sectionPart, rest] = splitOnce(target, "/");
  const section = sectionPart ?? target;
  if (rest === undefined) return section;
  const [, fieldPart] = splitOnce(rest, ".");
  const field = fieldTitle(fieldPart ?? null);
  return field === null ? section : `${section} · ${field}`;
}

function splitOnce(value: string, separator: string): [string, string | undefined] {
  const at = value.indexOf(separator);
  if (at < 0) return [value, undefined];
  return [value.slice(0, at), value.slice(at + separator.length)];
}

/** 三段切法（给 parseTarget 用）：`[前, 分隔符, 后]`，没有分隔符时后两段都是空串。 */
function splitTriple(value: string, separator: string): [string, string, string] {
  const at = value.indexOf(separator);
  if (at < 0) return [value, "", ""];
  return [value.slice(0, at), separator, value.slice(at + separator.length)];
}

function sectionTitle(sectionId: string, sections?: SectionView[]): string {
  const hit = sections?.find((section) => section.section_id === sectionId);
  return hit === undefined ? sectionId : `${sectionId} ${hit.title}`;
}
