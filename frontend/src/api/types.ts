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
  /** 这一包是谁交回的（§4.4 信封的 `packet_id`）。留痕行按它精确挂载。 */
  /** `null` = 修之前落库的老轮次（后端没存）：`dropped.ts` 会回退 (seat_id, pass_index) 启发式。 */
  packet_id: string | null;
}

/** 一条"没进卡片"的留痕：地址、席位、第几趟、原因、来源（`04` C5 后半）。 */
export interface DroppedView {
  handle: string;
  seat_id: string | null;
  pass_index: number;
  reason: string;
  source: "integrator" | "assembly" | "unaccounted" | string;
  /** 来自哪一包。`undefined` = 不来自模型回包（assembly 归因 / tool 失败），或老数据。 */
  packet_id?: string;
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
  /** 编号里有账本上的 SIMULATED（M11）：打「模拟评测依据」tag；老轮次为 undefined。 */
  simulation_based?: boolean;
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
  /**
   * 分析者交回的「我读了什么才切出这些主张」——复述卡在批准主张之前显示它，
   * 用户据此看出分析者读没读对章节。老行没有 → 空串。
   */
  analysis_basis: string;
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
  /** 模拟调研产物（M10）：没跑过是 null/undefined；抽屉靠它决定出不出现。 */
  sim_inquiry?: SimInquiryView | null;
  /** 模拟评测产物（M11）：没跑过是 null/undefined。 */
  sim_review?: SimReviewView | null;
}

export interface SimSliceView {
  local_id: string;
  situation: string;
  job: string;
  trigger: string;
  workaround: string;
  anchors: string[];
}

export interface SimHypothesisView {
  local_id: string;
  slice_id: string;
  statement: string;
  grounding: string;
  based_on: string[];
  falsification: string;
  evidence_needed: string;
}

export interface SimUnknownView {
  local_id: string;
  statement: string;
  why_it_matters: string;
  how_to_find_out: string;
}

export interface SimInquiryView {
  slices: SimSliceView[];
  hypotheses: SimHypothesisView[];
  unknowns: SimUnknownView[];
  runs: number;
  chosen: string[];
}

export interface SimWalkStepView {
  step_index: number;
  step_text: string;
  from_doc: string;
  action: string;
  outcome: "ok" | "stuck" | "guessed" | string;
  need: string;
}

export interface SimFrictionView {
  local_id: string;
  kind: string;
  entry_id: string;
  field: string;
  quote: string;
  not_documented: boolean;
  blocks_flow: boolean;
  note: string;
}

export interface SimAcceptanceCheckView {
  local_id: string;
  entry_id: string;
  status: "verified" | "unverified" | "contradicted" | string;
  note: string;
}

export interface SimFeatureReviewView {
  feature_slug: string;
  started: boolean;
  walkthrough: SimWalkStepView[];
  frictions: SimFrictionView[];
  acceptance_checks: SimAcceptanceCheckView[];
  verdict: "smooth" | "friction" | "blocked" | string;
  verdict_because: string[];
}

export interface SimPersonaReviewView {
  persona_id: string;
  title: string;
  features: SimFeatureReviewView[];
}

/** 三档**原始计数**（按 persona×功能 对）：不给百分比、不给分数。 */
export interface SimReviewSummaryView {
  smooth: number;
  friction: number;
  blocked: number;
}

export interface SimReviewView {
  runs: number;
  max_runs: number;
  evaluated_version: number | null;
  personas: SimPersonaReviewView[];
  summary: SimReviewSummaryView;
  advisories: string[];
}

export interface KeepSimResult {
  material_id: string;
  kept: string[];
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

/** 模板（一套章节集 + 三锚点；`02` §11.0.68）。创建页选择器（切片 2）读它。 */
export interface TemplateSectionView {
  section_id: string;
  title: string;
  purpose: string;
  role: string;
  slots: { name: string; title: string; kind: string }[];
}

export interface TemplateView {
  template_id: string;
  title: string;
  sections: TemplateSectionView[];
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
  /** 模拟评测（M11）：选择器的勾选上限；老后端为 undefined（下游回落 3/2）。 */
  max_sim_review_personas?: number;
  max_sim_review_features?: number;
  /** 讨论线程的 turn 上限（M13）；老后端为 undefined（下游回落 12）。 */
  max_discussion_turns?: number;
}

/** `GET /schema`：现行结构，不是文档内容。 */
export interface SchemaView {
  sections: SectionSchemaView[];
  feature_fields: FeatureFieldSchemaView[];
  limits: LimitsView;
  /** 讨论区的三个固定角色（`dlg.*`）。**必填**：灵感区不抄一份角色表（M9）。 */
  discussion_roles: DiscussionRoleSchema[];
  /** 五个固定模拟使用者（M11）：评测选择器读它；老后端为 undefined（回落空表）。 */
  personas?: PersonaSchemaView[];
  /** 模板库（`02` §11.0.68）：创建页选择器（切片 2）读它；老后端为 undefined。 */
  templates?: TemplateView[];
}

/** `GET /schema` 里的一个模拟使用者（M11）。 */
export interface PersonaSchemaView {
  persona_id: string;
  title: string;
  situation: string;
}

/** `GET /schema` 里的一个讨论角色。 */
export interface DiscussionRoleSchema {
  role_id: string;
  title: string;
  focus: string;
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

/** 一趟调研里的一条候选（M8）：假设带证伪两行，盲区带下一步。 */
export interface CandidateView {
  candidate_id: string;
  kind: "hypothesis" | "blind_spot" | string;
  statement: string;
  state: "new" | "kept" | "dismissed" | string;
  kept_as: "seed" | "material" | null;
  /** 收下为材料时的 material_id；收下为种子时为空。 */
  kept_ref: string | null;
  grounding: "document" | "material" | "invented" | null;
  based_on: string[];
  falsification: string | null;
  evidence_needed: string | null;
  next_step: string | null;
}

/** 开聊时带的问卷题快照（只存题面与原因，不读轮）。 */
export interface DiscussionQuestionView {
  title: string;
  why: string | null;
}

/** 讨论里的一轮（一说就是一**对**：user 与 assistant 各一条，`index` 连续）。 */
export interface TurnView {
  index: number;
  role: "user" | "assistant" | string;
  text: string;
  based_on: string[];
  /** 谁说的：用户 turn 写 "user"，席位 turn 写 role_id（M18；旧行缺省按 assistant 显示）。 */
  author: string;
  /** 第几轮 panel 轮次（M18；say_pair 记当前轮，旧行缺省 1）。 */
  round: number;
}

/** 讨论总结出的一张灵感卡（M9）。过目状态在 `state`/`kept_as`（M12，与候选同构）。 */
export interface CardView {
  card_id: string;
  statement: string;
  /** `document` = 文档里有据的（必带 E 编号）；`invented` = 凭空推演（收下默认不勾）。 */
  grounding: "document" | "invented" | string;
  based_on: string[];
  /** 收下为材料时的 material_id；收种子/忽略时空。 */
  kept_ref: string | null;
  state: "new" | "kept" | "dismissed" | string;
  kept_as: "seed" | "material" | null;
}

/** panel 成员（M18）：哪个角色、从第几轮开始在场。单成员即旧 1:1 线程。 */
export interface ParticipantView {
  role_id: string;
  role_title: string;
  joined_round: number;
}

/** 一轮的综合（M18）：共识 / 分歧 / 未决三组清单，只归纳不新开观点。 */
export interface RoundSummaryView {
  index: number;
  agreed: string[];
  disagreed: string[];
  open: string[];
}

/** 一条讨论线程（M9）：列表与展开共用它；真身在后端。 */
export interface ThreadView {
  thread_id: string;
  project_id: string;
  topic: string;
  question: DiscussionQuestionView | null;
  role_id: string;
  role_title: string;
  status: "open" | "closed" | string;
  turns: TurnView[];
  cards: CardView[];
  /** 开题调研底稿条数（M13）：真身在探索表，这里只给数。 */
  findings_count: number;
  created_at: string;
  /** panel 成员（M18；旧行读回即单成员）。 */
  participants: ParticipantView[];
  /** 每轮综合（M18；只增，给人看的副栏）。 */
  summaries: RoundSummaryView[];
}

/** 一趟探索运行（M8）：过目台与历史都读它。 */
export interface ExplorationView {
  exploration_id: string;
  project_id: string;
  kind: "inquiry" | string;
  status: "running" | "done" | "failed" | "abandoned" | string;
  scope_kind: "entry" | "document" | string;
  scope_ref: string | null;
  scope_text: string | null;
  created_at: string;
  failure_reason: string | null;
  abandon_reason: string | null;
  /** 超上限被解析层截掉的候选条数（界面照实说）。 */
  overflow_count: number;
  candidates: CandidateView[];
}
