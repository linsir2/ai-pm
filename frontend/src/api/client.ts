/* 唯一的 HTTP 出口。界面里不许散落 fetch。 */

import type {
  ConfirmResponse,
  DocumentView,
  ProjectSummaryView,
  RoundView,
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
};
