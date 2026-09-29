/* 把界面上的作答变成请求体。纯函数：不碰 DOM、不发请求。

   两条来自设计的规矩落在这里：
   1. 问卷是"交卷语义"——没答也没显式跳过的题按跳过提交，后果由题面写清；
   2. 草案默认勾选"你说过的 + 它替你判断的"，**它提议的默认不勾**。 */

import { listFeatures } from "../document/structure";
import type { AnswerBatchView, ClaimView, DocumentView, DraftView, RoundView } from "./types";
import { featureFields } from "../state/schema";

export interface AnswerDraft {
  selected: string[];
  text: string;
  skipped?: boolean;
}

export interface ClaimEdit {
  label: string;
  target_label: string;
}

export type AwaitingMode =
  | "input"
  | "opening"
  | "questionnaire"
  | "draft"
  | "loading"
  | "done"
  | "failed"
  | "abandoned";

/** 可以直接改到的标签。猜测与决策要补字段（假设 / 拍板人），复述卡改不了。 */
const BASE_LABELS = ["fact", "need", "goal", "constraint", "preference"];

export function selectableLabels(currentLabel: string): string[] {
  if (currentLabel === "guess") return ["guess"];
  if (currentLabel === "decision") return ["decision", ...BASE_LABELS];
  return BASE_LABELS;
}

export function buildAnswers(round: RoundView, drafts: Record<string, AnswerDraft>) {
  const answers = round.questions
    .filter((question) => {
      const draft = drafts[question.question_id];
      return (
        draft !== undefined &&
        draft.skipped !== true &&
        (draft.selected.length > 0 || draft.text.trim() !== "")
      );
    })
    .map((question) => {
      const draft = drafts[question.question_id]!;
      return {
        question_id: question.question_id,
        selected: draft.selected,
        text: draft.text.trim(),
      };
    });

  const touched = new Set(answers.map((item) => item.question_id));
  const skipped = round.questions
    .map((question) => question.question_id)
    .filter((questionId) => !touched.has(questionId));
  return { form_id: round.form_id ?? "", answers, skipped };
}

export function unansweredCount(
  round: RoundView,
  drafts: Record<string, AnswerDraft>,
): number {
  return buildAnswers(round, drafts).skipped.length;
}

/** 一批问卷答成什么样（F3）：一行字，给人看。 */
export function answerBatchSummary(batch: AnswerBatchView): string {
  const head = `已答 ${batch.answered}`;
  return batch.skipped > 0 ? `${head} · 跳过 ${batch.skipped}` : head;
}

export function defaultAccepted(draft: DraftView): string[] {
  return draft.proposals
    .filter((proposal) => proposal.origin_type !== "proposed")
    .map((proposal) => proposal.proposal_id);
}

/** `04-未决.md` C4 的"本批全接受"：含"它提议的"那组（默认不勾的那些）。 */
export function allProposalIds(draft: DraftView): string[] {
  return draft.proposals.map((proposal) => proposal.proposal_id);
}

/** 与"全接受"配对的那一半：一条都不勾（用户自己挑）。 */
export const NO_PROPOSALS: string[] = [];

export function defaultSeats(round: RoundView): string[] {
  return round.nominated_seats.map((seat) => seat.seat_id);
}

export interface SeatNominationSummary {
  nominated: number;
  available: number;
  /** agent = 名单来自分析者的判断；catalog = 老轮次的目录回落（界面要照实说）。 */
  source: "agent" | "catalog" | "empty";
}

/** 复述卡顶上那句话要的数字：分析者挑了几个、还能自己加几个、名单是哪来的。 */
export function seatNominationSummary(round: RoundView): SeatNominationSummary {
  const first = round.nominated_seats[0];
  return {
    nominated: round.nominated_seats.length,
    available: round.available_seats.length,
    source:
      first === undefined ? "empty" : first.source === "catalog" ? "catalog" : "agent",
  };
}

export function initialClaimEdits(round: RoundView): Record<string, ClaimEdit> {
  return Object.fromEntries(
    round.claims.map((claim) => [
      claim.claim_id,
      { label: claim.label, target_label: claim.target_label },
    ]),
  );
}

/** 请求体里的去处形状（与后端 `ClaimEditRequest.target` 一一对应）。 */
export interface ClaimTargetBody {
  zone: "body" | "preference";
  section_id?: string;
  feature_slug?: string;
  field?: string;
}

/**
 * 去处 label → 请求体。label 的形状由后端 `Target.label()` 决定，共四种：
 * `preference` / `s8` / `s8.performance`（章节槽）/ `s5/order-export.acceptance`（功能字段）。
 */
export function targetBody(label: string): ClaimTargetBody {
  if (label === "preference") return { zone: "preference" };

  const slash = label.indexOf("/");
  if (slash >= 0) {
    const section_id = label.slice(0, slash);
    const rest = label.slice(slash + 1);
    const dot = rest.indexOf(".");
    if (dot < 0) return { zone: "body", section_id, feature_slug: rest };
    return {
      zone: "body",
      section_id,
      feature_slug: rest.slice(0, dot),
      field: rest.slice(dot + 1),
    };
  }

  const dot = label.indexOf(".");
  if (dot >= 0) {
    return { zone: "body", section_id: label.slice(0, dot), field: label.slice(dot + 1) };
  }
  return { zone: "body", section_id: label };
}

export interface TargetOption {
  /** 去处 label（`s5/order-export.acceptance` 这种）——直接当 `<select>` 的 value。 */
  value: string;
  text: string;
}

/**
 * 复述卡的去处下拉：偏好区 + 13 章 + **每个已存在功能的 12 个字段**（F7）。
 *
 * 字段清单来自 `/schema`（拿不到就用 `FALLBACK_FIELD_TITLES`），所以"验收标准那块还不存在"
 * 也能选——真正建块发生在确认写入那一刻（domain 自己建）。
 */
export function targetOptions(doc: DocumentView | null): TargetOption[] {
  const options: TargetOption[] = [{ value: "preference", text: "偏好区" }];
  if (doc === null) return options;

  for (const section of doc.sections) {
    options.push({ value: section.section_id, text: `${section.section_id} ${section.title}` });
  }
  for (const feature of listFeatures(doc)) {
    const slug = feature.block.feature_slug ?? feature.slug;
    for (const field of featureFields()) {
      options.push({
        value: `s5/${slug}.${field.name}`,
        text: `${feature.name} · ${field.title ?? field.name}`,
      });
    }
  }
  return options;
}

/**
 * 只提交**改过的**条目。去处跟着标签走：改成偏好就必须落到偏好区
 * （后端 `Claim.__post_init__` 会因"标签与分区不符"直接拒掉，别让它走到那一步）。
 */
export function buildClaimEdits(round: RoundView, edits: Record<string, ClaimEdit>) {
  return round.claims
    .filter((claim: ClaimView) => {
      const edit = edits[claim.claim_id];
      if (edit === undefined) return false;
      return edit.label !== claim.label || edit.target_label !== claim.target_label;
    })
    .map((claim) => {
      const edit = edits[claim.claim_id]!;
      // 标签是偏好时去处强制走偏好区（后端会因"标签与分区不符"直接拒）
      const target =
        edit.label === "preference"
          ? ({ zone: "preference" } as ClaimTargetBody)
          : targetBody(edit.target_label);
      return { claim_id: claim.claim_id, label: edit.label, target };
    });
}

export function buildOpeningBody(
  round: RoundView,
  seats: string[],
  edits: Record<string, ClaimEdit>,
) {
  return { seats, claim_edits: buildClaimEdits(round, edits) };
}

/** 底部那一块该是什么：输入框 / 复述卡 / 抽屉 / 等数据 / 结束。 */
export function awaitingMode(round: RoundView | null): AwaitingMode {
  if (round === null) return "input";
  if (round.step === "failed") return "failed";
  if (round.step === "abandoned") return "abandoned";
  if (round.step === "done") return "done";
  if (round.waiting_on === "opening_card") return "opening";
  if (round.waiting_on === "questionnaire") {
    return round.questions.length > 0 ? "questionnaire" : "loading";
  }
  if (round.waiting_on === "draft_card") {
    return round.draft === null ? "loading" : "draft";
  }
  return "loading";
}

export function integrationSummary(round: RoundView): string {
  const parts: string[] = [];
  const questions = round.questions.length;
  const proposals = round.draft?.proposals.length ?? 0;
  if (questions > 0) parts.push(`出了 ${questions} 题`);
  if (proposals > 0) parts.push(`出了 ${proposals} 条候选`);
  if (round.list_items.length > 0) parts.push(`${round.list_items.length} 条先记成清单`);
  return parts.length === 0 ? "整合完成" : `整合：${parts.join(" · ")}`;
}
