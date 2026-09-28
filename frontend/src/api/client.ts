/* 唯一的 HTTP 出口。界面里不许散落 fetch。 */

import type {
  ConfirmResponse,
  DiffView,
  DocumentView,
  EditResult,
  EvidenceLookupView,
  ProjectSummaryView,
  RollbackResult,
  RoundView,
  VersionListView,
  WorkspaceView,
} from "./types";

const BASE = "/api";

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
  text?: string;
}

export const api = {
  health: () => request<{ status: string; model: string }>("/health"),

  listProjects: () => request<ProjectSummaryView[]>("/projects"),

  createProject: (title: string) =>
    request<ProjectSummaryView>("/projects", {
      method: "POST",
      body: JSON.stringify({ title }),
    }),

  workspace: (projectId: string) =>
    request<WorkspaceView>(`/projects/${projectId}/workspace`),

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

  startRound: (projectId: string, input: string) =>
    request<RoundView>(`/projects/${projectId}/rounds`, {
      method: "POST",
      body: JSON.stringify({ input }),
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
