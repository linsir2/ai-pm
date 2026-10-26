// @vitest-environment jsdom
import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { CandidateView, ExplorationView, RoundView } from "../api/types";
import { useWorkspaceData } from "./useWorkspaceData";

// 全仓首个 vi.mock：factory 会被 hoist 到文件顶，顶层变量一律用 vi.hoisted 包
//（inline 的 vi.fn() / import 进来的 ApiError 在 factory 里直接引用会炸）。
// 其余导出走 importOriginal 原样透出，只换掉 api 单例。
const { decideWebRequest, getRound, keepCandidate, startExploration, startRound, rounds } =
  vi.hoisted(() => ({
    decideWebRequest: vi.fn(),
    getRound: vi.fn(),
    keepCandidate: vi.fn(),
    startExploration: vi.fn(),
    startRound: vi.fn(),
    rounds: vi.fn(),
  }));
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      listProjects: async () => [],
      schema: async () => null,
      decideWebRequest,
      getRound,
      keepCandidate,
      startExploration,
      startRound,
      rounds,
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
  rounds.mockResolvedValue([]);
});

it("keep 成功才把种子文本与 id 落进输入框", async () => {
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    await result.current.keepCandidate("x-1", "H1", "seed");
  });
  expect(result.current.roundInput).toContain("运营按客户名找单");
  expect(result.current.pendingSeeds).toEqual(["exploration:x-1#H1"]);
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

it("拿去验证（M13）：拿卡片原话起一趟聚焦调研，结果替换进探索列表", async () => {
  startExploration.mockResolvedValue(exploration());
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    result.current.selectProject("p1");
  });
  await act(async () => {
    await result.current.verifyCard("导出按客户名筛会不会被滥用");
  });

  expect(startExploration).toHaveBeenCalledWith("p1", {
    kind: "inquiry",
    scope: { kind: "document", ref: null, text: "导出按客户名筛会不会被滥用" },
  });
  expect(result.current.explorations.map((item) => item.exploration_id)).toEqual(["x-1"]);
  expect(result.current.state.error).toBeNull();
});

it("拿去验证失败：探索列表原样，错误可见（不假装跑过）", async () => {
  startExploration.mockRejectedValueOnce(new ApiError("exploration_busy", "同一项目同时只允许一趟调研"));
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    result.current.selectProject("p1");
  });
  await act(async () => {
    await result.current.verifyCard("导出按客户名筛会不会被滥用");
  });

  expect(result.current.explorations).toEqual([]);
  expect(result.current.state.error).toContain("同一项目同时只允许一趟调研");
});

it("切项目把上一个项目的项目级状态清干净（输入/搜索/待带种子/历史轮）", async () => {
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    result.current.selectProject("p1");
  });
  await act(async () => {
    result.current.setRoundInput("上个项目没发出去的草稿");
    result.current.setSearchQuery("上个项目的搜索词");
  });
  await act(async () => {
    await result.current.keepCandidate("x-1", "H1", "seed"); // 收下为种子：进输入框 + pendingSeeds
  });
  expect(result.current.roundInput).toContain("运营按客户名找单");
  expect(result.current.pendingSeeds).toEqual(["exploration:x-1#H1"]);

  await act(async () => {
    result.current.selectProject("p2");
  });

  expect(result.current.roundInput).toBe("");
  expect(result.current.searchQuery).toBe("");
  expect(result.current.searchResults).toBeNull();
  expect(result.current.pendingSeeds).toEqual([]);
  expect(result.current.pendingMaterials).toEqual([]);
});

it("起轮成功后立刻刷新轮次列表（新轮不再显示成本轮、开局时长不丢）", async () => {
  const started = {
    round_id: "r-new",
    project_id: "p1",
    user_input: "做个导出",
    step: "started",
    waiting_on: "none",
    seat_round_count: 0,
    seats: [],
    active_seats: [],
    nominated_seats: [],
    available_seats: [],
    claims: [],
    seat_packets: [],
    dropped: [],
    questions: [],
    asked_questions: [],
    list_items: [],
    draft: null,
    advisories: [],
    failure_reason: null,
    failure_at_step: null,
    retry_from: null,
    abandon_reason: null,
    confirmed_version_no: null,
    form_id: null,
    answer_sets: [],
    materials: [],
    web_queries: [],
    web_queries_chosen: [],
    web_search_available: false,
    web_search_network_access: false,
    write_scope: [],
    contradictions: [],
    proposed_offered: null,
    proposed_accepted: null,
    proposal_ignore_streak: 0,
    analysis_basis: "",
  } as unknown as RoundView;
  startRound.mockResolvedValue(started);
  rounds.mockResolvedValue([{ round_id: "r-new" }]);
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    result.current.selectProject("p1");
  });
  rounds.mockClear(); // 清掉 selectProject 那一次；只看 startRound 之后有没有刷
  await act(async () => {
    result.current.setRoundInput("做个导出");
  });
  await act(async () => {
    await result.current.startRound();
  });

  expect(rounds).toHaveBeenCalledWith("p1");
  expect(result.current.rounds.map((item) => item.round_id)).toEqual(["r-new"]);
});

function decidedRound(state: string, note: string | null = null): RoundView {
  return {
    round_id: "r1",
    project_id: "p1",
    user_input: "想法",
    step: "seats_running",
    waiting_on: "none",
    seat_round_count: 1,
    seats: [],
    active_seats: [],
    nominated_seats: [],
    available_seats: [],
    claims: [],
    seat_packets: [],
    dropped: [],
    questions: [],
    asked_questions: [],
    list_items: [],
    draft: null,
    advisories: [],
    failure_reason: null,
    failure_at_step: null,
    retry_from: null,
    abandon_reason: null,
    confirmed_version_no: null,
    form_id: null,
    answer_sets: [],
    materials: [],
    web_queries: [],
    web_requests: [
      {
        request_id: "w1",
        query: "RBAC 权限模型",
        why: "要查行业通行做法",
        origin: "seat",
        seat_id: "seat.permissions",
        pass_index: 1,
        state,
        result_note: note,
        created_at: null,
      },
    ],
    allow_web: false,
    web_search_available: true,
    web_search_network_access: true,
  } as unknown as RoundView;
}

async function seedRound(
  result: { current: ReturnType<typeof useWorkspaceData> },
): Promise<void> {
  startRound.mockResolvedValueOnce(decidedRound("proposed"));
  await act(async () => {
    result.current.selectProject("p1");
  });
  await act(async () => {
    result.current.setRoundInput("想法");
  });
  await act(async () => {
    await result.current.startRound();
  });
}

it("决策成功：用返回的 RoundView 换掉当前这一轮，并清掉在途申请", async () => {
  decideWebRequest.mockResolvedValueOnce(decidedRound("done", "搜到 3 条结果"));
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await seedRound(result);

  await act(async () => {
    await result.current.decideWebRequest("r1", "w1", "approve");
  });

  expect(decideWebRequest).toHaveBeenCalledWith("r1", "w1", "approve");
  expect(result.current.state.round?.web_requests[0]?.state).toBe("done");
  expect(result.current.state.round?.web_requests[0]?.result_note).toBe("搜到 3 条结果");
  expect(result.current.pendingWebRequest).toBeNull();
  expect(result.current.state.error).toBeNull();
});

it("拒绝在途：pendingWebRequest 记住被点的是 reject（R25）", async () => {
  let resolveDecision!: (view: RoundView) => void;
  decideWebRequest.mockReturnValueOnce(
    new Promise<RoundView>((resolve) => {
      resolveDecision = resolve;
    }),
  );
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await seedRound(result);

  act(() => {
    result.current.decideWebRequest("r1", "w1", "reject");
  });

  expect(result.current.pendingWebRequest).toEqual({
    requestId: "w1",
    decision: "reject",
  });

  await act(async () => {
    resolveDecision(decidedRound("rejected"));
    await Promise.resolve();
  });
  expect(result.current.pendingWebRequest).toBeNull();
});

it("非工具失败：错误可见，并主动 refetch 把 failed 的真实状态读回来（R17）", async () => {
  decideWebRequest.mockRejectedValueOnce(new ApiError("tool_failed", "执行中断"));
  getRound.mockResolvedValueOnce(decidedRound("failed", "执行中断"));
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await seedRound(result);

  await act(async () => {
    await result.current.decideWebRequest("r1", "w1", "approve");
  });

  expect(getRound).toHaveBeenCalledWith("r1");
  expect(result.current.state.round?.web_requests[0]?.state).toBe("failed");
  expect(result.current.state.round?.web_requests[0]?.result_note).toBe("执行中断");
  expect(result.current.state.error).toContain("执行中断");
  expect(result.current.pendingWebRequest).toBeNull();
});
