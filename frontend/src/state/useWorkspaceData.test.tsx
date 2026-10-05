// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { CandidateView, ExplorationView } from "../api/types";
import { useWorkspaceData } from "./useWorkspaceData";

// 全仓首个 vi.mock：factory 会被 hoist 到文件顶，顶层变量一律用 vi.hoisted 包
//（inline 的 vi.fn() / import 进来的 ApiError 在 factory 里直接引用会炸）。
// 其余导出走 importOriginal 原样透出，只换掉 api 单例。
const { keepCandidate } = vi.hoisted(() => ({ keepCandidate: vi.fn() }));
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      listProjects: async () => [],
      schema: async () => null,
      keepCandidate,
    },
  };
});
vi.mock("./useEvents", () => ({ useEvents: () => ({ connection: "idle" as const }) }));

function candidate(): CandidateView {
  return {
    candidate_id: "H1",
    kind: "hypothesis",
    statement: "运营按客户名找单",
    state: "kept",
    kept_as: "seed",
    kept_ref: null,
    grounding: "invented",
    based_on: [],
    falsification: "访谈没人这么找",
    evidence_needed: "两个访谈",
    next_step: null,
  };
}

function exploration(): ExplorationView {
  return {
    exploration_id: "x-1",
    project_id: "p1",
    kind: "inquiry",
    status: "done",
    scope_kind: "document",
    scope_ref: null,
    scope_text: null,
    created_at: "2026-10-05T00:00:00",
    failure_reason: null,
    abandon_reason: null,
    overflow_count: 0,
    candidates: [candidate()],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  keepCandidate.mockResolvedValue(exploration());
});

it("keep 成功才把种子文本与 id 落进输入框", async () => {
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    await result.current.keepCandidate("x-1", "H1", "seed");
  });
  expect(result.current.roundInput).toContain("运营按客户名找单");
  expect(result.current.pendingSeeds).toEqual(["x-1#H1"]);
});

it("keep 失败：输入框与 pendingSeeds 原样不动（毒种子进不来）", async () => {
  keepCandidate.mockRejectedValueOnce(new ApiError("invariant:X3", "候选 H1 已经是「已删掉」，不能再收下"));
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    await result.current.keepCandidate("x-1", "H1", "seed");
  });
  expect(result.current.roundInput).toBe("");
  expect(result.current.pendingSeeds).toEqual([]);
});
