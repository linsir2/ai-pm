// @vitest-environment jsdom
/* S1：别的动作在途时，讨论「开跑」不许静默吞掉；列表读失败不许伪装成空。
   这里只 mock 一条完整启动链（workspace + 四个只读投影），其余照真模块走。 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { ThreadView } from "../api/types";
import { useWorkspaceData } from "./useWorkspaceData";

const { startDiscussion, attachResearchSources, discussions } = vi.hoisted(() => ({
  startDiscussion: vi.fn(),
  attachResearchSources: vi.fn(),
  discussions: vi.fn(),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      listProjects: async () => [],
      schema: async () => null,
      workspace: async () => ({
        project: {
          project_id: "p1",
          title: "项目",
          version_no: 1,
          updated_at: "2026-10-31T00:00:00+00:00",
          template_id: "",
          template_title: "",
        },
        document: {
          document_id: "doc-1",
          project_id: "p1",
          title: "文档",
          version_no: 1,
          sections: [],
          preference_entries: [],
        },
        round: null,
        readiness: [],
      }),
      rounds: async () => [],
      materials: async () => [],
      explorations: async () => [],
      discussions,
      startDiscussion,
      attachResearchSources,
    },
  };
});
vi.mock("./useEvents", () => ({ useEvents: () => ({ connection: "idle" as const }) }));

function thread(over: Partial<ThreadView> = {}): ThreadView {
  return {
    thread_id: "d-1",
    project_id: "p1",
    topic: "导出要不要做筛选",
    question: null,
    role_id: "dlg.challenger",
    role_title: "反方",
    status: "open",
    turns: [],
    cards: [],
    findings_count: 0,
    created_at: "2026-10-31T00:00:00+00:00",
    participants: [],
    summaries: [],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  discussions.mockResolvedValue([]);
});

describe("S1 · 别的动作在途时开 panel", () => {
  it("pending=research：不发 startDiscussion、返回 null、错误说明当前在做什么", async () => {
    const { result } = renderHook(() => useWorkspaceData());
    await act(async () => {});
    await act(async () => {
      result.current.selectProject("p1");
    });
    await act(async () => {});

    attachResearchSources.mockReturnValue(new Promise(() => undefined));
    await act(async () => {
      void result.current.attachResearchSources("r-1", "x-1", ["E1"]);
    });
    expect(result.current.pending).toBe("research");
    expect(result.current.busy).toBe(true);

    let threadId: string | null = "sentinel";
    await act(async () => {
      threadId = await result.current.startPanel(
        { topic: "导出优化", role_ids: ["dlg.challenger"] },
        false,
      );
    });

    expect(threadId).toBeNull();
    expect(startDiscussion).not.toHaveBeenCalled();
    expect(result.current.state.error).toContain("当前正在");
    expect(result.current.state.error).toContain("研究");
  });

  it("空闲时正常开 panel：只发一次", async () => {
    startDiscussion.mockResolvedValue(thread());
    const { result } = renderHook(() => useWorkspaceData());
    await act(async () => {});
    await act(async () => {
      result.current.selectProject("p1");
    });
    await act(async () => {});
    expect(result.current.busy).toBe(false);

    let threadId: string | null = null;
    await act(async () => {
      threadId = await result.current.startPanel(
        { topic: "导出优化", role_ids: ["dlg.challenger"] },
        false,
      );
    });

    expect(startDiscussion).toHaveBeenCalledTimes(1);
    expect(threadId).toBe("d-1");
    expect(result.current.state.error).toBeNull();
  });
});

describe("S1 · 讨论列表读失败", () => {
  it("保留旧列表并显示错误，不伪装成「还没开过讨论」", async () => {
    discussions.mockResolvedValue([thread()]);
    const { result } = renderHook(() => useWorkspaceData());
    await act(async () => {});
    await act(async () => {
      result.current.selectProject("p1");
    });
    await act(async () => {});
    expect(result.current.discussions.map((item) => item.thread_id)).toEqual(["d-1"]);

    discussions.mockRejectedValueOnce(new ApiError("discussions_unavailable", "讨论列表读不到"));
    await act(async () => {
      result.current.backToLatest();
    });
    await act(async () => {});

    expect(result.current.discussions.map((item) => item.thread_id)).toEqual(["d-1"]);
    expect(result.current.state.error).toContain("讨论列表读不到");
  });
});
