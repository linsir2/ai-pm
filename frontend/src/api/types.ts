/* 后端 DTO 的形状。手写（P2 不引 openapi-typescript），字段名照抄设计计划 §5.2 与
   `application/dto.py` 的投影。改这份文件之前先改后端投影，否则界面会静默错位。 */

export type Step =
  | "started"
  | "claims_ready"
  | "seats_running"
  | "awaiting_answers"
  | "awaiting_confirm"
  | "done"
  | "failed";

export type WaitingOn = "none" | "opening_card" | "questionnaire" | "draft_card";

export interface Entry {
  entry_id: string;
  text: string;
  refs: string[];
}

export interface BlockView {
  block_id: string;
  kind: string;
  entries: Entry[];
  children: BlockView[];
  field: string | null;
  feature_slug: string | null;
  manually_edited: boolean;
  /** 这一块能不能有多条：单值字段只能改写，其余可以加与删。 */
  multiple: boolean;
}

export interface SectionView {
  section_id: string;
  title: string;
  blocks: BlockView[];
}

export interface DocumentView {
  document_id: string;
  project_id: string;
  title: string;
  version_no: number;
  sections: SectionView[];
  preference_entries: Entry[];
}

export interface ClaimView {
  claim_id: string;
  eid: string;
  text: string;
  label: string;
  target_label: string;
  raw_span: string;
  assumption_note: string | null;
}

export interface OpinionView {
  local_id: string;
  form: "question" | "judgment" | string;
  about: string;
  claim: string;
  because: string;
  eids: string[];
  basis_note: string | null;
  eid: string | null;
}

export interface SeatPacketView {
  seat_id: string;
  title: string;
  status: "completed" | "pending" | string;
  pass_index: number;
  basis: string;
  opinions: OpinionView[];
  judgment_count: number;
  question_count: number;
}

export interface QuestionView {
  question_id: string;
  kind: "gap" | "conflict" | "decision" | string;
  type: "single" | "multi" | "text" | string;
  title: string;
  why: string;
  target_label: string;
  options: [string, string][];
  consequence_if_skipped: string;
  eids: string[];
  asked_by: string[];
}

export interface ProposalView {
  proposal_id: string;
  entry_id: string;
  op: "add" | "replace" | "remove" | string;
  target_label: string;
  text: string;
  origin_type: "user" | "derived" | "proposed" | "system_default" | string;
  eids: string[];
  rationale: string | null;
  kind: string | null;
  warning: string | null;
}

export interface DraftView {
  draft_id: string;
  groups: [string, ProposalView[]][];
  proposals: ProposalView[];
  gaps: string[];
}

export interface SeatSuggestionView {
  seat_id: string;
  title: string;
  focus: string;
  /** 常驻席位：每轮都请（不是模型判断，是目录里的配置）。 */
  always_on: boolean;
  /** 你这轮的话里命中了它的哪些关键词（同样不是模型判断）。 */
  matched_keywords: string[];
}

export interface RoundView {
  round_id: string;
  project_id: string;
  user_input: string;
  step: Step | string;
  waiting_on: WaitingOn | string;
  seat_round_count: number;
  seats: string[];
  nominated_seats: SeatSuggestionView[];
  claims: ClaimView[];
  seat_packets: SeatPacketView[];
  questions: QuestionView[];
  list_items: string[];
  draft: DraftView | null;
  advisories: string[];
  failure_reason: string | null;
  confirmed_version_no: number | null;
  form_id: string | null;
}

export interface ProjectSummaryView {
  project_id: string;
  title: string;
  version_no: number;
  updated_at: string;
}

export interface WorkspaceView {
  project: ProjectSummaryView;
  document: DocumentView;
  round: RoundView | null;
}

export interface WorkspaceEvent {
  name: string;
  project_id: string;
  round_id: string | null;
  payload: Record<string, unknown>;
  occurred_at: string;
  seq: number;
}

export interface ConfirmResponse {
  version_no: number;
  round: RoundView;
}

/** 手改文档的返回：新版本号 + 改完的文档（界面一次往返就够）。 */
export interface EditResult {
  version_no: number;
  written_block_ids: string[];
  entry_id: string;
  document: DocumentView;
}
