// @vitest-environment jsdom
/* 讨论区接进数据编排（M9-T6）：说一轮成对落、失败成对不落、收卡标上 kept_ref。
   范式照 `useWorkspaceData.test.tsx`（M8.1 已验证的 `vi.hoisted`）：全仓唯一一处
   `vi.mock` 在那边，这里照它的形状再开一处——每个测试文件各 mock 各的，模块缓存互不干扰。 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { ThreadView } from "../api/types";
import { useWorkspaceData } from "./useWorkspaceData";

const { sayToThread, keepDiscussionCard } = vi.hoisted(() => ({
  sayToThread: vi.fn(),
  keepDiscussionCard: vi.fn(),
}));

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    api: {
      listProjects: async () => [],
      schema: async () => null,
      sayToThread,
      keepDiscussionCard,
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
    created_at: "2026-10-05T10:00:00+00:00",
    ...over,
  };
}

/** 一说之后的样子：turns 是一对（user 与 assistant 各一条，index 连续）。 */
function afterOneSay(over: Partial<ThreadView> = {}): ThreadView {
  return thread({
    turns: [
      { index: 0, role: "user", text: "先只做全量", based_on: [] },
      { index: 1, role: "assistant", text: "那筛选留到有人骂再加", based_on: ["E1"] },
    ],
    ...over,
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  sayToThread.mockResolvedValue(afterOneSay());
});

it("说一轮成功：一对轮次都落进线程（user 与 assistant 各一条）", async () => {
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});

  await act(async () => {
    await result.current.sayToThread("d-1", "先只做全量");
  });

  const turns = result.current.discussions[0]?.turns ?? [];
  expect(turns.map((item) => item.role)).toEqual(["user", "assistant"]);
  expect(result.current.state.error).toBeNull();
});

it("说一轮失败：成对都不落，线程原样（模型坏包时后端一条也不写）", async () => {
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});
  await act(async () => {
    await result.current.sayToThread("d-1", "先只做全量");
  });

  sayToThread.mockRejectedValueOnce(new ApiError("model_packet_unparsable", "两次都没解析出来"));
  await act(async () => {
    await result.current.sayToThread("d-1", "再来一次");
  });

  // 还是那两条（不是三条，也不是清空）
  expect(result.current.discussions[0]?.turns).toHaveLength(2);
  expect(result.current.state.error).toContain("两次都没解析出来");
});

it("收下卡片成功：那一张的 kept_ref 标上材料 id", async () => {
  keepDiscussionCard.mockResolvedValue(
    afterOneSay({
      cards: [
        {
          card_id: "C1",
          statement: "导出先只做全量，筛选等有人骂再加",
          grounding: "invented",
          based_on: [],
          kept_ref: "m-1",
        },
      ],
    }),
  );
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});

  await act(async () => {
    await result.current.keepDiscussionCard("d-1", "C1");
  });

  expect(result.current.discussions[0]?.cards[0]?.kept_ref).toBe("m-1");
});

it("收卡失败：kept_ref 仍是空（界面不会自己给一张卡盖上已收下）", async () => {
  keepDiscussionCard.mockRejectedValueOnce(new ApiError("invariant:ThreadClosed", "线程已关闭"));
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});

  await act(async () => {
    await result.current.keepDiscussionCard("d-1", "C1");
  });

  expect(result.current.discussions[0]?.cards[0]?.kept_ref).toBeUndefined();
  expect(result.current.state.error).toContain("线程已关闭");
});
