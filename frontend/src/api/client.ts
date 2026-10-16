/* 唯一的 HTTP 出口。界面里不许散落 fetch。 */

import type {
  ConfirmResponse,
  DiffView,
  DocumentView,
  EditResult,
  EvidenceLookupView,
  ExplorationView,
  KeepSimResult,
  MaterialSummaryView,
  ProjectSummaryView,
  RollbackResult,
  RoundView,
  RoundHeadView,
  SchemaView,
  SearchResultsView,
  TemplateSectionPayload,
  TemplateView,
  ThreadView,
  VersionListView,
  WorkspaceView,
} from "./types";

const BASE = "/api";

/** 当前登录用户的投影（后端 `GetMe` 回什么就读什么）。 */
export interface AuthUser {
  user_id: string;
  username: string;
  role: string;
  status: string;
}

/** 一个用户的用量投影（`GET /auth/usage`）：其项目下轮次的模型调用行数。 */
export interface UsageRow {
  user_id: string;
  username: string;
  calls: number;
}

/**
 * 管理后台那一屏的一行（`GET /auth/stats`）。
 *
 * **为什么不在 `/auth/usage` 上加字段**：那个名字装不下"最近活跃"和"审批时间"，
 * 而它是既有窄口径契约（测试与别的调用方都钉着）。于是新端点，旧的照旧。
 *
 * `failed` / `avg_duration_ms` 才是管理员真正先看的：调用数好看但 8.8% 超时、
 * 平均一条 67 秒，光看"183 次调用"这两件事都看不见。
 */
export interface AdminStatsRow {
  user_id: string;
  username: string;
  role: string;
  status: string;
  projects: number;
  rounds: number;
  calls: number;
  failed: number;
  avg_duration_ms: number | null;
  total_tokens: number;
  active_sessions: number;
  /** 后端给的是 ISO 串；这里**不**转 Date——表格里要显示原始时区那一行。 */
  last_seen_at: string | null;
  created_at: string | null;
  approved_at: string | null;
  approved_by: string | null;
}

/** 会话失效（任一端点回 401）时的**一处**通知口：AuthProvider 注册它清身份，
   界面据此回到登录页——省的每个调用各写一遍 401 处理。 */
let notifyUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(handler: (() => void) | null): void {
  notifyUnauthorized = handler;
}

/** 起轮的请求体（工具层 C-2 起带材料 id；M12 起种子是 `来源:id#条目id`）。 */
export interface StartBody {
  input: string;
  material_ids: string[];
  /** 过目产物收下的种子：`exploration:id#候选id` / `discussion:id#卡id`。缺省 = 不带。 */
  seed_ids: string[];
}

/** 发起调研的请求体（M8）。 */
export interface ExplorationBody {
  kind: "inquiry";
  scope: { kind: "entry" | "document"; ref: string | null; text: string | null };
}

/** 开一条讨论线程的请求体（M9/M18，与后端 `StartDiscussionRequest` 一一对应）。 */
export interface DiscussionBody {
  topic: string;
  /** 1–4 个角色（M18 panel；单元素即旧 1:1 开聊）。 */
  role_ids: string[];
  /** 问卷题快照：只当开场白，**可空**（讨论 tab 自由开聊时不带）。 */
  question: { title: string; why: string | null } | null;
  /** 「先跑一轮调研」的底稿趟次（M13）：不传 = 无底稿。 */
  finding_exploration_id: string | null;
}

/** 真模型一轮要跑几十秒；给一个宽上限，避免请求永远挂着没人管。 */
const REQUEST_TIMEOUT_MS = 300_000;

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * 把"非 2xx 的回包"变成 `ApiError`（`P29` 眼睛检查 F10）。
 *
 * 三种情况分得开：
 * - 后端自己的错误：JSON 里有 `code`/`message` → 照原样用（界面直接显示那句话）；
 * - **不是 JSON**：后端没起来 / 正在重启 / dev 代理回的 500 —— 以前只说"请求失败（HTTP 500）"，
 *   用户会以为自己的项目丢了（真跑实拍：首页显示"先建一个项目"）。现在明说"后端没有正常回话"；
 * - JSON 但没有 message：回落成带状态码的人话。
 */
export function apiErrorFrom(status: number, body: unknown, fallback: string): ApiError {
  const payload = (body ?? {}) as Record<string, unknown>;
  const hasCode = typeof payload.code === "string";
  const hasMessage = typeof payload.message === "string";
  if (hasCode && hasMessage) {
    return new ApiError(
      payload.code as string,
      payload.message as string,
      (payload.details as Record<string, unknown> | undefined) ?? {},
    );
  }
  if (!hasCode && !hasMessage) {
    return new ApiError(
      "backend_unreachable",
      `后端没有正常回话（HTTP ${status}）——它可能在重启，或者没起来；刷新一下试试。`,
    );
  }
  return new ApiError(
    hasCode ? (payload.code as string) : "http_error",
    hasMessage ? (payload.message as string) : `${fallback}（HTTP ${status}）`,
  );
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  // 外部 signal（M18「停止这一轮」用）：调用方 abort 时把内部 controller 一起掐掉，
  // 并记下"是调用方停的"——好把它的报错译成"已停止"而不是"超时"。
  const external = init?.signal ?? null;
  let stoppedByCaller = false;
  const onExternalAbort = () => {
    stoppedByCaller = true;
    controller.abort();
  };
  if (external !== null) {
    if (external.aborted) onExternalAbort();
    else external.addEventListener("abort", onExternalAbort);
  }
  const timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch (error) {
    clearTimeout(timer);
    if (error instanceof DOMException && error.name === "AbortError") {
      if (stoppedByCaller) throw new ApiError("aborted", "已停止这一轮。");
      throw new ApiError("timeout", "请求超时了，后端可能还在跑；刷新页面看看这一轮的进度。");
    }
    throw new ApiError("network_error", "连不上后端（先确认它起在 8000 端口）。");
  } finally {
    external?.removeEventListener("abort", onExternalAbort);
  }
  clearTimeout(timer);

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    // 401 = 没身份 / 会话过期（登录失败也走这里）：统一抛 `unauthorized`，
    // 并通知**一处**（AuthProvider 清身份 → 界面回登录页）。
    if (response.status === 401) {
      notifyUnauthorized?.();
      const message =
        typeof body.message === "string" ? body.message : "未登录或登录已过期";
      throw new ApiError("unauthorized", message, (body.details as Record<string, unknown>) ?? {});
    }
    throw apiErrorFrom(response.status, body, "请求失败");
  }
  // **空 body 不许调 `.json()`**——204 / 205 / 空字符串 body 上它抛
  // `SyntaxError: Unexpected end of JSON input`，而那不是"请求失败"，是成功。
  // 真踩过：`DELETE /projects/{id}/memory/{id}` 返回 204，于是「忘掉一条记忆」
  // 每次都抛，`useWorkspaceData.ts:300` 之后的刷新永不执行——后端删了，界面没删，
  // 下一轮提示词还带着那条记忆。
  //
  // 先取文本再判空，而不是只看 `status === 204`：非 204 的成功回包也可能是空的。
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

/** 导出交付用 Markdown：拿文本自己触发一次下载（不新开窗口，不依赖后端文件名）。 */
async function download(path: string, fallbackName: string): Promise<void> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    throw apiErrorFrom(response.status, body, "导出失败");
  }
  const text = await response.text();
  const url = URL.createObjectURL(new Blob([text], { type: "text/markdown;charset=utf-8" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fallbackName;
  anchor.click();
  URL.revokeObjectURL(url);
}

export interface OpeningBody {
  seats: string[];
  /** 复述卡上勾中的联网检索词；空数组 = 这一轮不联网。 */
  web_queries: string[];
  claim_edits: {
    claim_id: string;
    label: string;
    target: { zone: string; section_id?: string; feature_slug?: string; field?: string };
  }[];
}

export interface AnswersBody {
  form_id: string;
  answers: { question_id: string; text: string; selected: string[] }[];
  skipped: string[];
}

/** 写去处的形状（`{section_id, feature_slug, field}`）：字段块不存在也能加。 */
export interface TargetRequest {
  zone: "body" | "preference";
  section_id?: string;
  feature_slug?: string;
  field?: string;
}

export interface EditBody {
  op: "add" | "replace" | "remove";
  entry_id?: string | null;
  block_id?: string | null;
  target?: TargetRequest | null;
  text?: string;
}

export const api = {
  health: () => request<{ status: string; model: string }>("/health"),

  // ── 认证与账号（Task 3 端点）─────────────────────────────────────────────
  /** 读当前会话（cookie 自动带）：未登录一律 401 → 抛 `unauthorized`。 */
  me: () => request<AuthUser>("/auth/me"),

  /** 登录：成功后 cookie 由后端 `Set-Cookie` 落地，前端不碰 token。 */
  login: (username: string, password: string) =>
    request<AuthUser>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  /** 注册：落一个 pending 用户（要管理员审批后才登得进）。 */
  register: (username: string, password: string) =>
    request<AuthUser>("/auth/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),

  /** 登出：后端删 cookie 并吊销会话。 */
  logout: () =>
    request<{ status: string }>("/auth/logout", { method: "POST", body: "{}" }),

  /** 管理员：全部用户（基础字段）。 */
  users: () => request<{ users: AuthUser[] }>("/auth/users"),

  /** 管理员：每个用户的用量（其项目下轮次的模型调用行数，join 聚合）。 */
  usage: () => request<{ usage: UsageRow[] }>("/auth/usage"),

  /** 管理员：每用户一行的活动读模型（项目/轮次/失败/耗时/token/会话/最近活跃）。 */
  stats: () => request<{ stats: AdminStatsRow[] }>("/auth/stats"),

  /** 管理员：审批通过（pending → active）。 */
  approve: (userId: string) =>
    request<AuthUser>("/auth/approve", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),

  /** 管理员：拒绝（pending → rejected）。 */
  reject: (userId: string) =>
    request<AuthUser>("/auth/reject", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),

  /** 管理员：封禁（置 banned 并当场吊销该用户全部会话）。 */
  ban: (userId: string) =>
    request<AuthUser>("/auth/ban", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    }),

  schema: () => request<SchemaView>("/schema"),

  listProjects: () => request<ProjectSummaryView[]>("/projects"),

  createProject: (title: string, templateId?: string) =>
    request<ProjectSummaryView>("/projects", {
      method: "POST",
      body: JSON.stringify(
        templateId === undefined ? { title } : { title, template_id: templateId },
      ),
    }),

  /** 模板库（`02` §11.0.68）：创建页选择器（切片 2）用；parity 双向门要求每个端点都有 client。 */
  listTemplates: () => request<TemplateView[]>("/templates"),

  getTemplate: (templateId: string) =>
    request<TemplateView>(`/templates/${encodeURIComponent(templateId)}`),

  createTemplate: (title: string, sections: TemplateSectionPayload[]) =>
    request<TemplateView>("/templates", {
      method: "POST",
      body: JSON.stringify({ title, sections }),
    }),

  /** 整份替换一套模板（`02` §11.0.70）。内置那份锁死，后端回 409。 */
  updateTemplate: (templateId: string, title: string, sections: TemplateSectionPayload[]) =>
    request<TemplateView>(`/templates/${encodeURIComponent(templateId)}`, {
      method: "PUT",
      body: JSON.stringify({ title, sections }),
    }),

  /** 删一套自建模板。已经用它建好的项目照常打开（项目存的是模板快照）。 */
  deleteTemplate: (templateId: string) =>
    request<void>(`/templates/${encodeURIComponent(templateId)}`, { method: "DELETE" }),

  workspace: (projectId: string) =>
    request<WorkspaceView>(`/projects/${projectId}/workspace`),

  /** 这个项目走过的轮次（最新在前）：闭环区顶上那串时间线。 */
  rounds: (projectId: string) => request<RoundHeadView[]>(`/projects/${projectId}/rounds`),

  /** 这个项目有哪些材料（只回摘要，不含块正文）。 */
  materials: (projectId: string) =>
    request<MaterialSummaryView[]>(`/projects/${projectId}/attachments`),

  /** 删一份材料。**没被引用过才准删**——被引用过后端回 409（消息里列出编号）。 */
  deleteMaterial: (projectId: string, materialId: string) =>
    request<MaterialSummaryView[]>(
      `/projects/${projectId}/attachments/${encodeURIComponent(materialId)}`,
      { method: "DELETE" },
    ),

  /** 用当前解析器把这份材料的原始字节重读一遍（不用重传文件）。返回刷新后的列表。 */
  reparseMaterial: (projectId: string, materialId: string) =>
    request<MaterialSummaryView[]>(
      `/projects/${projectId}/attachments/${encodeURIComponent(materialId)}/reparse`,
      { method: "POST" },
    ),

  /** 按网址抓一份网页材料（P27）：后端真抓、真解析；抓不到会带回一句人话。 */
  fetchMaterial: (projectId: string, url: string) =>
    request<MaterialSummaryView>(`/projects/${projectId}/attachments/fetch`, {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  /** 上传一份材料：multipart，解析在后端做（真解析：文本 / PDF / HTML / Word / Excel）。 */
  uploadMaterial: async (projectId: string, file: File): Promise<MaterialSummaryView> => {
    const form = new FormData();
    form.append("file", file, file.name);
    const response = await fetch(`${BASE}/projects/${projectId}/attachments`, {
      method: "POST",
      body: form,          // 不设 Content-Type：让浏览器带 multipart 边界
    });
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
      throw apiErrorFrom(response.status, body, "上传失败");
    }
    return (await response.json()) as MaterialSummaryView;
  },

  /** 在这个项目里搜：条目 / 功能 / 编号 / 席位发言（F6）。空查询后端直接回空。 */
  search: (projectId: string, query: string) =>
    request<SearchResultsView>(
      `/projects/${projectId}/search?q=${encodeURIComponent(query)}`,
    ),

  document: (projectId: string) =>
    request<DocumentView>(`/projects/${projectId}/document`),

  getRound: (roundId: string) => request<RoundView>(`/rounds/${roundId}`),

  /** 按编号查处方：本地索引查不到时用它（跨轮编号、以及"这条现在被谁用着"）。 */
  evidence: (projectId: string, eid: string) =>
    request<EvidenceLookupView>(
      `/projects/${projectId}/evidence/${encodeURIComponent(eid)}`,
    ),

  versions: (projectId: string) =>
    request<VersionListView>(`/projects/${projectId}/versions`),

  versionDiff: (projectId: string, from: number, to: number) =>
    request<DiffView>(`/projects/${projectId}/versions/${from}/diff?to=${to}`),

  rollbackVersion: (projectId: string, versionNo: number) =>
    request<RollbackResult>(`/projects/${projectId}/versions/${versionNo}/rollback`, {
      method: "POST",
      body: "{}",
    }),

  startRound: (projectId: string, body: StartBody) =>
    request<RoundView>(`/projects/${projectId}/rounds`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** 这个项目走过的探索（最新在前，≤20）：过目台与历史都读它（M8）。 */
  explorations: (projectId: string) =>
    request<ExplorationView[]>(`/projects/${projectId}/explorations`),

  /** 发起一趟调研：请求内跑完，失败也返回 200 + status=failed（可重试）。 */
  startExploration: (projectId: string, body: ExplorationBody) =>
    request<ExplorationView>(`/projects/${projectId}/explorations`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** 重跑一趟失败的调研：只补没跑完的那次调用。 */
  retryExploration: (explorationId: string) =>
    request<ExplorationView>(
      `/explorations/${encodeURIComponent(explorationId)}/retry`,
      { method: "POST" },
    ),

  /** 放弃一趟调研：先记状态、再取消正在跑的调用。 */
  abandonExploration: (explorationId: string) =>
    request<ExplorationView>(
      `/explorations/${encodeURIComponent(explorationId)}/abandon`,
      { method: "POST" },
    ),

  /** 收下一条候选：`seed` = 预填下一轮输入；`material` = 落成材料。 */
  keepCandidate: (explorationId: string, candidateId: string, as: "seed" | "material") =>
    request<ExplorationView>(
      `/explorations/${encodeURIComponent(explorationId)}/candidates/${encodeURIComponent(candidateId)}/keep`,
      { method: "POST", body: JSON.stringify({ as }) },
    ),

  /** 删掉一条候选：终态，不过目台、不回流。 */
  dismissCandidate: (explorationId: string, candidateId: string) =>
    request<ExplorationView>(
      `/explorations/${encodeURIComponent(explorationId)}/candidates/${encodeURIComponent(candidateId)}/dismiss`,
      { method: "POST" },
    ),

  /** 这个项目开过的讨论线程（最新在前，≤20 条）：讨论 tab 读它（M9）。 */
  discussions: (projectId: string) =>
    request<ThreadView[]>(`/projects/${projectId}/discussions`),

  /**
   * 开一条讨论线程：同（项目，归一化主题，角色集合）已有 open 线程时**幂等返那一条**
   * （后端 D4 形态）——所以连点两次不会开出两条。
   */
  startDiscussion: (projectId: string, body: DiscussionBody) =>
    request<ThreadView>(`/projects/${projectId}/discussions`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** 追问一轮：模型跑完、校验通过才原子记一对（user＋assistant）。 */
  sayToThread: (discussionId: string, text: string) =>
    request<ThreadView>(`/discussions/${encodeURIComponent(discussionId)}/say`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),

  /** 把这段讨论总结成 1-3 张灵感卡（无 body：总结对象就是整段讨论）。 */
  assembleCards: (discussionId: string) =>
    request<ThreadView>(`/discussions/${encodeURIComponent(discussionId)}/cards`, {
      method: "POST",
      body: "{}",
    }),

  /**
   * 收下一张卡：`seed` = 预填下一轮输入；`material` = 落一份材料（回流口径 SIMULATED）。
   * 同卡重复收下幂等；换去向 409（T5）。
   */
  keepDiscussionCard: (discussionId: string, cardId: string, as: "seed" | "material") =>
    request<ThreadView>(
      `/discussions/${encodeURIComponent(discussionId)}/cards/${encodeURIComponent(cardId)}/keep`,
      { method: "POST", body: JSON.stringify({ as }) },
    ),

  /** 忽略一张卡：终态——不进灵感箱、不回流（同卡重复忽略幂等）。 */
  dismissDiscussionCard: (discussionId: string, cardId: string) =>
    request<ThreadView>(
      `/discussions/${encodeURIComponent(discussionId)}/cards/${encodeURIComponent(cardId)}/dismiss`,
      { method: "POST" },
    ),

  /** 关掉一条线程：终态幂等（无 body）。 */
  closeDiscussion: (discussionId: string) =>
    request<ThreadView>(`/discussions/${encodeURIComponent(discussionId)}/close`, {
      method: "POST",
      body: "{}",
    }),

  /**
   * 跑一轮 panel 自主讨论（M18）：本轮在场者并行发言 → 综合 → 落库。
   * 一次只跑一轮；N 轮由前端循环驱动。失败保留已落库的 turn、无综合，重试补剩余。
   * `signal`（可选）：「停止这一轮」用它即时掐掉客户端等待（服务端也会 abort）。
   */
  runPanelRound: (discussionId: string, signal?: AbortSignal) =>
    request<ThreadView>(`/discussions/${encodeURIComponent(discussionId)}/run-round`, {
      method: "POST",
      body: "{}",
      signal,
    }),

  /** 中途拉人进 panel（M18）：下一轮生效，不追补、不重跑。 */
  joinPanel: (discussionId: string, roleId: string) =>
    request<ThreadView>(`/discussions/${encodeURIComponent(discussionId)}/join`, {
      method: "POST",
      body: JSON.stringify({ role_id: roleId }),
    }),

  /**
   * 停掉正在跑的那一轮（M18）：只让在途调用停下来，不写状态。
   * 已落库的 turn 照留，线程还是 open；没在跑幂等成功。返回是否真停掉了一轮。
   */
  abortPanelRun: (discussionId: string) =>
    request<{ aborted: boolean }>(
      `/discussions/${encodeURIComponent(discussionId)}/abort-run`,
      { method: "POST", body: "{}" },
    ),

  submitOpening: (roundId: string, body: OpeningBody) =>
    request<RoundView>(`/rounds/${roundId}/opening`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  submitAnswers: (roundId: string, body: AnswersBody) =>
    request<RoundView>(`/rounds/${roundId}/answers`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** 确认写入：`acceptedMemory` 是草案卡上勾中的**记忆提议**（M6，默认不勾 → 空）。 */
  confirm: (roundId: string, accepted: string[], acceptedMemory: string[] = []) =>
    request<ConfirmResponse>(`/rounds/${roundId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ accepted, accepted_memory: acceptedMemory }),
    }),

  /** 忘掉一条记忆（M6）：记忆抽屉上的按钮，删了以后下一轮提示词立刻不带它。 */
  forgetMemory: (projectId: string, memoryId: string) =>
    request<void>(`/projects/${projectId}/memory/${memoryId}`, { method: "DELETE" }),

  retry: (roundId: string) =>
    request<RoundView>(`/rounds/${roundId}/retry`, { method: "POST", body: "{}" }),

  /** 草案卡上的"继续深挖"：回到提问，把刚才没问上的题问出来（C6）。 */
  dig: (roundId: string) =>
    request<RoundView>(`/rounds/${roundId}/dig`, { method: "POST", body: "{}" }),

  /** 草案卡上的"去模拟调研"：轮内动作（不碰 step），跑完读回最新投影。 */
  runSimInquiry: (roundId: string) =>
    request<RoundView>(`/rounds/${roundId}/sim-inquiry`, { method: "POST", body: "{}" }),

  /** 收下模拟的勾选为材料（空选择后端直接拒；同选择重复收下幂等）。 */
  keepSimInquiry: (roundId: string, chosen: string[]) =>
    request<KeepSimResult>(`/rounds/${roundId}/sim-inquiry/keep`, {
      method: "POST",
      body: JSON.stringify({ chosen }),
    }),

  /** 跑一趟模拟评测（M11）：选中的模拟使用者与功能；跑完读回最新投影。 */
  runSimReview: (roundId: string, personaIds: string[], featureSlugs: string[]) =>
    request<RoundView>(`/rounds/${roundId}/sim-review`, {
      method: "POST",
      body: JSON.stringify({ persona_ids: personaIds, feature_slugs: featureSlugs }),
    }),

  /** 放弃这一轮：后端先记状态、再取消正在跑的模型调用。 */
  abandonRound: (roundId: string) =>
    request<RoundView>(`/rounds/${roundId}/abandon`, { method: "POST", body: "{}" }),

  /** 手改文档：改一条 / 加一条 / 删一条（一次改动一版）。 */
  editEntry: (projectId: string, body: EditBody) =>
    request<EditResult>(`/projects/${projectId}/edits`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** 导出交付用 Markdown（浏览器直接下载；文件名兜底用 ASCII，中文名由后端给头）。 */
  exportMarkdown: (projectId: string, versionNo: number) =>
    download(`/projects/${projectId}/export/markdown`, `deliverable-v${versionNo}.md`),
};
