/* 后端 DTO 的形状。手写（P2 不引 openapi-typescript），字段名照抄设计计划 §5.2 与
   `application/dto.py` 的投影。改这份文件之前先改后端投影，否则界面会静默错位。 */

export type Step =
  | "started"
  | "claims_ready"
  | "seats_running"
  | "awaiting_answers"
  | "awaiting_confirm"
  | "done"
  | "failed"
  | "abandoned";

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
  /** 字段的中文名（功能字段或章节槽）。真相源在后端，界面不再自己抄一份。 */
  field_title: string | null;
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

/** 一条"没进卡片"的留痕：地址、席位、第几趟、原因、来源（`04` C5 后半）。 */
export interface DroppedView {
  handle: string;
  seat_id: string | null;
  pass_index: number;
  reason: string;
  source: "integrator" | "assembly" | "unaccounted" | string;
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
  /** 阻塞型问题：不答就交不出去（后端按规则算的，不是模型说自己重要）。 */
  blocking: boolean;
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

/** 草案卡上"这一轮攒下的记忆"的一行（M6）。**提议**：默认不勾，勾了才随确认生效。 */
export interface MemoryProposalView {
  proposal_id: string;
  op: "add" | "update" | "merge" | "delete" | string;
  kind: "lesson" | "decision" | "rejected" | string;
  text: string;
  refs: string[];
  reason: string;
  target_id: string | null;
}

/** 已确认的一条记忆（记忆抽屉读它，M6）。 */
export interface MemoryEntryView {
  memory_id: string;
  kind: "lesson" | "decision" | "rejected" | string;
  text: string;
  refs: string[];
  round_id: string;
  updated_at: string;
}

export interface SeatSuggestionView {
  seat_id: string;
  title: string;
  focus: string;
  /** 分析者读完这一轮输入之后给的理由（主口径）。回落目录匹配时为空。 */
  why: string;
  /** agent = 分析者挑的；catalog = 老轮次的目录回落。 */
  source: "agent" | "catalog" | string;
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
  /** 这一趟实际跑的名单（缩编后的结果；用户编制仍是 `seats`）。 */
  active_seats: string[];
  nominated_seats: SeatSuggestionView[];
  /** 目录里没被提名、但用户想加就能加的席位。 */
  available_seats: SeatSuggestionView[];
  claims: ClaimView[];
  seat_packets: SeatPacketView[];
  /** 这一轮没进卡片的意见（三个来源合成一条通道，`04` C5 后半）。 */
  dropped: DroppedView[];
  /** **当前批**的题（界面用的就是这份：换一批就换一份）。 */
  questions: QuestionView[];
  /**
   * 本轮**问过的每一道题**（逐批追加，按出题顺序）——`answer_sets` 的问题侧。
   * 界面不用它；"这一轮到底问过什么"是整轮的事实（换批不该丢），判据与复盘看这份。
   */
  asked_questions: QuestionView[];
  list_items: string[];
  draft: DraftView | null;
  advisories: string[];
  failure_reason: string | null;
  /** 失败停在哪个 step（`failed` 之外的那个）——界面上译成人话给"失败在哪"。 */
  failure_at_step: string | null;
  /** 「点重试会从哪继续」的人话（后端对着 `RESUME_ROUTES` 写的；认不出 → null，不猜）。 */
  retry_from: string | null;
  /** 放弃这一轮的原因（终点状态；失败另有 failure_reason）。 */
  abandon_reason: string | null;
  confirmed_version_no: number | null;
  form_id: string | null;
  /** 这一轮每一批问卷答了什么（F3）：答了几条、跳了几条、答的是什么。 */
  answer_sets: AnswerBatchView[];
  /** 这一轮带进提示词的材料块（工具层 C-2）。 */
  materials: RoundMaterialView[];
  /**
   * 分析者提名的联网检索词（工具层 C-2b）：**只是提名**，勾了才会真去查。
   * 勾选状态由用户给（默认不勾），后端把"你勾过哪条"投影在 `chosen` 里。
   */
  web_queries: WebQueryView[];
  /** 服务端现在有没有可用的联网工具（没配密钥就是 false）——据此决定出不出现那一栏。 */
  web_search_available: boolean;
  /** 这一轮攒下的记忆提议（M6）：**提议**，界面上默认不勾。老轮次为 undefined。 */
  memory_proposals?: MemoryProposalView[];
}

export interface WebQueryView {
  query: string;
  why: string;
  chosen: boolean;
}

export interface AnswerBatchView {
  form_id: string;
  answered: number;
  skipped: number;
  answers: string[];
}

export interface ProjectSummaryView {
  project_id: string;
  title: string;
  version_no: number;
  updated_at: string;
}

export interface ReadinessItemView {
  section_id: string;
  code: string;
  title: string;
  detail: string;
  severity: "blocker" | "warn" | string;
}

export interface WorkspaceView {
  project: ProjectSummaryView;
  document: DocumentView;
  round: RoundView | null;
  /** 这份文档按合格标准还差什么（只读判据，不是模型判断）。 */
  readiness: ReadinessItemView[];
  /** 已确认的跨轮记忆（M6）：老工作台 payload 没有这个键时为 undefined。 */
  memory?: MemoryEntryView[];
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

export interface VersionSummaryView {
  version_no: number;
  trigger: "confirm" | "manual" | "rollback" | string;
  trigger_label: string;
  summary: string;
  created_at: string;
  changed_block_ids: string[];
}

export interface VersionListView {
  versions: VersionSummaryView[];
  current: number;
}

export interface EntrySnapshotView {
  entry_id: string;
  text: string;
  refs: string[];
}

export interface ChangedEntryView {
  before: EntrySnapshotView;
  after: EntrySnapshotView;
}

export interface DiffGroupView {
  target_label: string;
  added: EntrySnapshotView[];
  removed: EntrySnapshotView[];
  changed: ChangedEntryView[];
}

export interface DiffView {
  from_version_no: number;
  to_version_no: number;
  groups: DiffGroupView[];
}

/** 回到某一版的返回：新版本号 + 新文档（与手改同一条路）。 */
export interface RollbackResult {
  version_no: number;
  document: DocumentView;
}

export interface ChainItemView {
  eid: string;
  kind: string;
  kind_label: string;
  text: string;
  summary: string;
}

/** 按编号查处方：原话、来路、链条、被谁引用（点编号跨轮跳转靠它）。 */
export interface EvidenceLookupView {
  eid: string;
  kind: string;
  kind_label: string;
  text: string;
  summary: string;
  raw_span: string | null;
  rationale: string | null;
  derived_from: string[];
  round_id: string | null;
  seat_id: string | null;
  question_id: string | null;
  /** 外部材料的指称：`attachment:需求.md#c00` / URL（别的 kind 是 null）。 */
  external_ref: string | null;
  chain: ChainItemView[];
  used_by: string[];
}

/** 章节槽（只有 s8 有六个）。 */
export interface SlotSchemaView {
  name: string;
  title: string;
  kind: string;
}

export interface SectionSchemaView {
  section_id: string;
  title: string;
  /** 这一章是干什么的（真相源在后端 `domain/shared/sections.py`）。 */
  purpose: string;
  allows_features: boolean;
  slots: SlotSchemaView[];
}

export interface FeatureFieldSchemaView {
  name: string;
  title: string;
  kind: string;
  multiple: boolean;
  required: boolean;
}

/** 三处上限：界面显示"第 2/3 轮""未答 3/5 题"用，不再自己抄一份。 */
export interface LimitsView {
  max_seat_rounds: number;
  max_questionnaires: number;
  max_open_items_per_batch: number;
}

/** `GET /schema`：现行结构，不是文档内容。 */
export interface SchemaView {
  sections: SectionSchemaView[];
  feature_fields: FeatureFieldSchemaView[];
  limits: LimitsView;
}

/** 材料（工具层 C-2）。列表与上传回**摘要**（不含块正文）。 */
export interface MaterialSummaryView {
  material_id: string;
  origin: string;
  title: string;
  source_ref: string;
  media_type: string;
  bytes_len: number;
  chunk_count: number;
  chars: number;
  parser_id: string;
  parser_version: string;
  truncated: boolean;
  warnings: string[];
  /** 这份材料是用**旧解析器**读的吗（界面提示"建议重解析"）。 */
  stale: boolean;
  /**
   * 这份材料被**哪些编号**引用过（P19）：空 = 没被用过，可以删、可以就地重解析；
   * 有编号 = **冻结**（编号指向它里面的块，删了编号就指向空气）。
   */
  eids: string[];
}

export interface RoundMaterialChunkView {
  chunk_id: string;
  eid: string;
  anchor: string;
  text: string;
}

/** 本轮材料卡：只含**带进提示词**的那些块。 */
export interface RoundMaterialView {
  material_id: string;
  title: string;
  source_ref: string;
  origin: string;
  parser_id: string;
  chunk_count: number;
  chunks: RoundMaterialChunkView[];
  truncated: boolean;
  warnings: string[];
}

/** 时间线上的一行：一轮的头部（不看全文）。 */
export interface RoundHeadView {
  round_id: string;
  created_at: string;
  step: string;
  user_input: string;
  claims_count: number;
  questions_count: number;
  confirmed_version_no: number | null;
  /** 这一轮头 3 条主张的摘要（"上一轮你提过什么"）。 */
  claim_headlines: string[];
}

/** 一条搜索结果：`ref` 是"去哪儿找"（条目 id / 块 id / 编号 / 轮次 id）。 */
export interface SearchHitView {
  kind: "entry" | "feature" | "evidence" | "opinion" | string;
  label: string;
  ref: string;
  where: string;
}

export interface SearchResultsView {
  query: string;
  hits: SearchHitView[];
  truncated: boolean;
}
