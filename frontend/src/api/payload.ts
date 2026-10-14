/* 把界面上的作答变成请求体。纯函数：不碰 DOM、不发请求。

   两条来自设计的规矩落在这里：
   1. 问卷是"交卷语义"——没答也没显式跳过的题按跳过提交，后果由题面写清；
   2. 草案默认勾选"你说过的 + 它替你判断的"，**它提议的默认不勾**。 */

import { listFeatures } from "../document/structure";
import type {
  AnswerBatchView,
  ClaimView,
  DocumentView,
  DraftView,
  MaterialSummaryView,
  RoundMaterialView,
  RoundView,
} from "./types";
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
  webQueries: string[] = [],
) {
  // 检索词默认空 = 不联网：这是"没勾就一次网络调用都没有"这条口径的起点
  return { seats, claim_edits: buildClaimEdits(round, edits), web_queries: [...webQueries] };
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

/** 起轮的请求体：一句话 + 这一轮带进来的材料 id（工具层 C-2）+ 过目产物种子（M12）。 */
export interface StartBody {
  input: string;
  material_ids: string[];
  /** "收下为下一轮输入"的那些过目产物：`来源:id#条目id`（构造器见下）。 */
  seed_ids: string[];
}

/** 种子 id 的唯一产出点（M12 统一格式，与后端 `_resolve_seeds` 的解析口径一字对齐）。 */

export function explorationSeedId(explorationId: string, candidateId: string): string {
  return `exploration:${explorationId}#${candidateId}`;
}

export function discussionSeedId(threadId: string, cardId: string): string {
  return `discussion:${threadId}#${cardId}`;
}

export function buildStart(
  input: string,
  materials: MaterialSummaryView[],
  seeds: string[] = [],
): StartBody {
  return {
    input,
    material_ids: materials.map((item) => item.material_id),
    seed_ids: [...seeds],
  };
}

/**
 * 发起调研的锚点（M8）：要么钉在文档里某一条上，要么是整份文档。
 * `focus` 是条目原文——后端拿它当调研的题目。
 */
export type ExplorationAnchor =
  | { kind: "entry"; ref: string; focus: string }
  //: `focus` = 整份文档之上的聚焦说明（M13「拿去验证」：拿一张卡的原话去调研）
  | { kind: "document"; focus?: string };

/** `POST /projects/{id}/explorations` 的请求体（与后端 `StartExplorationRequest` 一一对应）。 */
export interface ExplorationBody {
  kind: "inquiry";
  scope: { kind: "entry" | "document"; ref: string | null; text: string | null };
}

/** 后端 `ExplorationScopeRequest.text` 的 `max_length=500`（码点）。
    超了直接 422，所以这里中央截断——任何长度的选段/条目都发得出去。
    截的是"补充说明"，完整条目后端按 `ref` 自己读（entry-scope），不丢信息。 */
export const FOCUS_LIMIT = 500;

export function truncateFocus(text: string): string {
  const chars = Array.from(text);
  if (chars.length <= FOCUS_LIMIT) return text;
  return `${chars.slice(0, FOCUS_LIMIT - 1).join("")}…`;
}

export function buildExplorationBody(anchor: ExplorationAnchor): ExplorationBody {
  if (anchor.kind === "entry") {
    return {
      kind: "inquiry",
      scope: { kind: "entry", ref: anchor.ref, text: truncateFocus(anchor.focus) },
    };
  }
  return {
    kind: "inquiry",
    scope: { kind: "document", ref: null, text: anchor.focus === undefined ? null : truncateFocus(anchor.focus) },
  };
}

/**
 * 收下一条候选后往输入框里追加种子文本：多条之间换行分隔，空文本不加分隔，
 * 用户已经打的内容原样保留。
 */
export function appendSeedText(current: string, text: string): string {
  if (text === "") return current;
  if (current === "") return text;
  return `${current}\n${text}`;
}

/**
 * 点搜索结果里的**材料**命中之后该去哪（`P18` C-2c-1）。
 *
 * 两种情况，都得说实话：
 * - 命中的块**在本轮材料里** → 能跳：滚到材料卡的那一块（`jumpToChunk`）；
 * - **不在**（那份材料还没被带进任何一轮） → 跳不了：它没有编号、也不在材料卡上，
 *   界面得说清"还没带进任何一轮"，不许假装能跳、也不许掉进"这条内容不在文档里"的兜底。
 */
export function materialHitTarget(
  chunkId: string,
  materials: RoundMaterialView[],
): { found: true; title: string; anchor: string } | { found: false } {
  for (const material of materials) {
    const chunk = material.chunks.find((item) => item.chunk_id === chunkId);
    if (chunk !== undefined) {
      return { found: true, title: material.title, anchor: chunk.anchor };
    }
  }
  return { found: false };
}

export interface AdvisoryGroup {
  text: string;
  count: number;
}

/**
 * 把**相同文本**的告警合成一条并带上条数（`P29` 眼睛检查 F3）。
 *
 * 真跑现场（草案抽屉）：三条提案都落在 s12，后端按"每条提案一句"吐出三句一模一样的话，
 * 界面上就是三个同样的黄条——用户分不清是哪三条，React 还会因为 `key` 重复报警告。
 * 顺序按**第一次出现**（先说的先说，不排序），不合并不同文本的告警。
 */
export function groupAdvisories(advisories: readonly string[]): AdvisoryGroup[] {
  const counts = new Map<string, number>();
  for (const item of advisories) {
    counts.set(item, (counts.get(item) ?? 0) + 1);
  }
  return [...counts.entries()].map(([text, count]) => ({ text, count }));
}

export interface EidSummary {
  /** 界面上真正列出来的那几个编号。 */
  shown: string[];
  /** 被省略掉的数量（0 = 全列出来了）。 */
  rest: number;
  /** 全量编号（`title` 用——省掉的也能看见，只是要点一下/悬停）。 */
  title: string;
}

/**
 * 编号列的收纳（`P29` 眼睛检查 F5）：材料被引用过之后 `eids` 可以到几十个，
 * 原样铺开会把整行撑到几百像素高、把表头挤成竖条。只列前 `limit` 个，其余报数。
 */
export function summarizeEids(eids: readonly string[], limit = 3): EidSummary {
  const shown = eids.slice(0, Math.max(0, limit));
  return {
    shown: [...shown],
    rest: Math.max(0, eids.length - shown.length),
    title: eids.join("、"),
  };
}
