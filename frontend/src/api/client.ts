/* 唯一的 HTTP 出口。界面里不许散落 fetch。 */

import type {
  ConfirmResponse,
  DiffView,
  DocumentView,
  EditResult,
  EvidenceLookupView,
  MaterialSummaryView,
  ProjectSummaryView,
  RollbackResult,
  RoundView,
  RoundHeadView,
  SchemaView,
  SearchResultsView,
  VersionListView,
  WorkspaceView,
} from "./types";

const BASE = "/api";

/** 起轮的请求体（工具层 C-2 起带材料 id）。 */
export interface StartBody {
  input: string;
  material_ids: string[];
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
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
      throw new ApiError("timeout", "请求超时了，后端可能还在跑；刷新页面看看这一轮的进度。");
    }
    throw new ApiError("network_error", "连不上后端（先确认它起在 8000 端口）。");
  }
  clearTimeout(timer);

  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    throw new ApiError(
      typeof body.code === "string" ? body.code : "http_error",
      typeof body.message === "string" ? body.message : `请求失败（HTTP ${response.status}）`,
      (body.details as Record<string, unknown> | undefined) ?? {},
    );
  }
  return (await response.json()) as T;
}

/** 导出交付用 Markdown：拿文本自己触发一次下载（不新开窗口，不依赖后端文件名）。 */
async function download(path: string, fallbackName: string): Promise<void> {
  const response = await fetch(`${BASE}${path}`);
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as Record<string, unknown>;
    throw new ApiError(
      typeof body.code === "string" ? body.code : "http_error",
      typeof body.message === "string" ? body.message : `导出失败（HTTP ${response.status}）`,
    );
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

export interface EditBody {
  op: "add" | "replace" | "remove";
  entry_id?: string | null;
  block_id?: string | null;
  /** 加一条时也能给完整去处（`{section_id, feature_slug, field}`）：字段块不存在也能加。 */
  target?: {
    zone: "body" | "preference";
    section_id?: string;
    feature_slug?: string;
    field?: string;
  } | null;
  text?: string;
}

export const api = {
  health: () => request<{ status: string; model: string }>("/health"),

  schema: () => request<SchemaView>("/schema"),

  listProjects: () => request<ProjectSummaryView[]>("/projects"),

  createProject: (title: string) =>
    request<ProjectSummaryView>("/projects", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),

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
      throw new ApiError(
        typeof body.code === "string" ? body.code : "http_error",
        typeof body.message === "string" ? body.message : `上传失败（HTTP ${response.status}）`,
        (body.details as Record<string, unknown> | undefined) ?? {},
      );
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

  confirm: (roundId: string, accepted: string[]) =>
    request<ConfirmResponse>(`/rounds/${roundId}/confirm`, {
      method: "POST",
      body: JSON.stringify({ accepted }),
    }),

  retry: (roundId: string) =>
    request<RoundView>(`/rounds/${roundId}/retry`, { method: "POST", body: "{}" }),

  /** 草案卡上的"继续深挖"：回到提问，把刚才没问上的题问出来（C6）。 */
  dig: (roundId: string) =>
    request<RoundView>(`/rounds/${roundId}/dig`, { method: "POST", body: "{}" }),

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
