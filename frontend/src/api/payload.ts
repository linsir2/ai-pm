/* 把界面上的作答变成请求体。纯函数：不碰 DOM、不发请求。

   两条来自设计的规矩落在这里：
   1. 问卷是"交卷语义"——没答也没显式跳过的题按跳过提交，后果由题面写清；
   2. 草案默认勾选"你说过的 + 它替你判断的"，**它提议的默认不勾**。 */

import { sectionIdOf } from "../labels";
import type { ClaimView, DraftView, RoundView } from "./types";

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
  | "failed";

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

export function defaultAccepted(draft: DraftView): string[] {
  return draft.proposals
    .filter((proposal) => proposal.origin_type !== "proposed")
    .map((proposal) => proposal.proposal_id);
}

export function defaultSeats(round: RoundView): string[] {
  return round.nominated_seats.map((seat) => seat.seat_id);
}

export function initialClaimEdits(round: RoundView): Record<string, ClaimEdit> {
  return Object.fromEntries(
    round.claims.map((claim) => [
      claim.claim_id,
      { label: claim.label, target_label: sectionIdOf(claim.target_label) },
    ]),
  );
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
      return (
        edit.label !== claim.label ||
        edit.target_label !== sectionIdOf(claim.target_label)
      );
    })
    .map((claim) => {
      const edit = edits[claim.claim_id]!;
      if (edit.label === "preference") {
        return { claim_id: claim.claim_id, label: edit.label, target: { zone: "preference" } };
      }
      return {
        claim_id: claim.claim_id,
        label: edit.label,
        target: { zone: "body", section_id: edit.target_label },
      };
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
