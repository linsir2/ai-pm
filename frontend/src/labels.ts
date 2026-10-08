/* 后端只给枚举值，中文词只在这里映射一次。
   加一个映射之前先问：后端有没有这个值？没有就别在前端发明。 */

import type { SectionView } from "./api/types";
import { fieldTitle } from "./state/schema";

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

export const WAITING_TEXT: Record<string, string> = {
  none: "没人需要动手",
  opening_card: "等你在复述卡上纠正",
  questionnaire: "等你交问卷",
  draft_card: "等你勾草案",
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

export function waitingText(waitingOn: string): string {
  return WAITING_TEXT[waitingOn] ?? waitingOn;
}

export function opinionFormText(form: string): string {
  return OPINION_FORM_TEXT[form] ?? form;
}

/** `s5` / `s5/feat-slug` / `s5/feat-slug.acceptance` / `preference` → 人话。 */
export function targetText(target: string, sections?: SectionView[]): string {
  if (target === "preference") return "偏好区";
  const [sectionPart, rest] = splitOnce(target, "/");
  const title = sectionTitle(sectionPart ?? target, sections);
  if (rest === undefined) return title;
  const [slug, fieldPart] = splitOnce(rest, ".");
  const field = fieldTitle(fieldPart ?? null);
  return field === null ? `${title} · ${slug}` : `${title} · ${slug} · ${field}`;
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

function sectionTitle(sectionId: string, sections?: SectionView[]): string {
  const hit = sections?.find((section) => section.section_id === sectionId);
  return hit === undefined ? sectionId : `${sectionId} ${hit.title}`;
}
