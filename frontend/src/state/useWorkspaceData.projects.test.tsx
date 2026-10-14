// @vitest-environment jsdom
/* 多项目收尾二批：createProject 对齐 selectProject（reset+booting+bootError+返回 summary）；
   loadWorkspace 及三个同形回包加归属 guard（晚到的回包不许覆盖新项目）。
   自带 mock（本仓惯例：每个测试文件各 mock 各的）。 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type {
  ProjectSummaryView,
  RoundHeadView,
  WorkspaceView,
} from "../api/types";
import { useWorkspaceData } from "./useWorkspaceData";

const { createProject, workspace, rounds, materials, explorations, discussions, abandonRound, startRound } =
  vi.hoisted(() => ({
    createProject: vi.fn(),
    workspace: vi.fn(),
    rounds: vi.fn(),
    materials: vi.fn(),
    explorations: vi.fn(),
    discussions: vi.fn(),
    abandonRound: vi.fn(),
    startRound: vi.fn(),
  }));
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      listProjects: async () => [],
      schema: async () => null,
      createProject,
      workspace,
      rounds,
      materials,
      explorations,
      discussions,
      abandonRound,
      startRound,
    },
  };
});
vi.mock("./useEvents", () => ({ useEvents: () => ({ connection: "idle" as const }) }));

function summary(project_id: string): ProjectSummaryView {
  return { project_id, title: `项目${project_id}`, version_no: 1, updated_at: "2026-10-15T00:00:00" };
}

function ws(project_id: string): WorkspaceView {
  return {
    project: summary(project_id),
    document: {} as unknown as WorkspaceView["document"],
    round: null,
    readiness: [],
    memory: [],
  };
}

function head(round_id: string): RoundHeadView {
  return { round_id } as unknown as RoundHeadView;
}

beforeEach(() => {
  vi.clearAllMocks();
  rounds.mockImplementation(async (pid: string) => [head(`r-${pid}`)]);
  materials.mockResolvedValue([]);
  explorations.mockResolvedValue([]);
  discussions.mockResolvedValue([]);
});

it("新建成功：旧项目状态清干净，返回新 summary，自动可进（调用方凭返回值 setListing(false)）", async () => {
  createProject.mockResolvedValue(summary("p2"));
  workspace.mockImplementation(async (pid: string) => ws(pid));
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    result.current.setRoundInput("旧项目的草稿");
    result.current.addSeed("exploration:x-1#H1", "旧项目的种子话");
  });
  expect(result.current.pendingSeeds).toEqual(["exploration:x-1#H1"]);

  let created: ProjectSummaryView | null | undefined;
  await act(async () => {
    created = await result.current.createProject("新项目");
  });

  expect(created?.project_id).toBe("p2");
  expect(result.current.state.projectId).toBe("p2");
  expect(result.current.roundInput).toBe("");
  expect(result.current.pendingSeeds).toEqual([]);
  expect(result.current.bootError).toBeNull();
});

it("新建失败：错误进 bootError（创建屏只看它），不抛", async () => {
  createProject.mockRejectedValueOnce(new ApiError("unknown_project", "建项目失败"));
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});

  let created: ProjectSummaryView | null | undefined = summary("x");
  await act(async () => {
    created = await result.current.createProject("新项目");
  });

  expect(created).toBeNull();
  expect(result.current.bootError).toContain("建项目失败");
});

it("快速切换：先选的后返回，晚到回包丢弃（URL/态/写目标全是后选的）", async () => {
  let resolveWsA!: (v: WorkspaceView) => void;
  workspace.mockImplementation((pid: string) =>
    pid === "pA" ? new Promise<WorkspaceView>((r) => { resolveWsA = r; }) : Promise.resolve(ws(pid)),
  );
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    result.current.selectProject("pA");
  });
  await act(async () => {
    result.current.selectProject("pB");
  });
  expect(result.current.state.projectId).toBe("pB");
  expect(result.current.rounds.map((item) => item.round_id)).toEqual(["r-pB"]);
  await act(async () => {
    resolveWsA(ws("pA"));
  });

  expect(result.current.state.projectId).toBe("pB");
  expect(result.current.rounds.map((item) => item.round_id)).toEqual(["r-pB"]);
});

it("abandon 在途时新轮落地：旧 abandon 回包丢弃（不倒灌）", async () => {
  workspace.mockImplementation(async (pid: string) => ws(pid));
  const r2 = { round_id: "r2", project_id: "pB", step: "started" };
  startRound.mockResolvedValue(r2 as never);
  let resolveAbandon!: (v: unknown) => void;
  abandonRound.mockImplementation(
    () => new Promise((resolve) => { resolveAbandon = resolve; }),
  );
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    result.current.selectProject("pB");
  });
  await act(async () => {
    result.current.setRoundInput("开新轮");
  });
  await act(async () => {
    void result.current.abandon("r1");
  });
  await act(async () => {
    await result.current.startRound();
  });
  expect(result.current.state.round?.round_id).toBe("r2");
  await act(async () => {
    resolveAbandon({ round_id: "r1", project_id: "pB", step: "abandoned" });
  });

  expect(result.current.state.round?.round_id).toBe("r2");
});
