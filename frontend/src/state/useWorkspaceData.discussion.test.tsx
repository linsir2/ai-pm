// @vitest-environment jsdom
/* 讨论区接进数据编排（M9/M12）：说一轮成对落、失败成对不落、
   收卡（种子/材料）与忽略都替换线程，种子成功才预填输入框。
   范式照 `useWorkspaceData.test.tsx`（M8.1 已验证的 `vi.hoisted`）：全仓唯一一处
   `vi.mock` 在那边，这里照它的形状再开一处——每个测试文件各 mock 各的，模块缓存互不干扰。 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "../api/client";
import type { CardView, ThreadView } from "../api/types";
import { useWorkspaceData } from "./useWorkspaceData";

const { sayToThread, keepDiscussionCard, dismissDiscussionCard } = vi.hoisted(() => ({
  sayToThread: vi.fn(),
  keepDiscussionCard: vi.fn(),
  dismissDiscussionCard: vi.fn(),
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
      dismissDiscussionCard,
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

function card(over: Partial<CardView> = {}): CardView {
  return {
    card_id: "C1",
    statement: "导出先只做全量，筛选等有人骂再加",
    grounding: "invented",
    based_on: [],
    kept_ref: null,
    state: "new",
    kept_as: null,
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

it("收下为材料成功：state/kept_as/kept_ref 落进线程，不动输入框", async () => {
  keepDiscussionCard.mockResolvedValue(
    afterOneSay({
      cards: [card({ state: "kept", kept_as: "material", kept_ref: "m-1" })],
    }),
  );
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});

  await act(async () => {
    await result.current.keepDiscussionCard("d-1", "C1", "material");
  });

  const kept = result.current.discussions[0]?.cards[0];
  expect([kept?.state, kept?.kept_as, kept?.kept_ref]).toEqual([
    "kept",
    "material",
    "m-1",
  ]);
  expect(result.current.roundInput).toBe("");
  expect(result.current.pendingSeeds).toEqual([]);
});

it("收下为种子成功：预填输入框并记下 `discussion:线程#卡` 种子 id", async () => {
  keepDiscussionCard.mockResolvedValue(
    afterOneSay({ cards: [card({ state: "kept", kept_as: "seed" })] }),
  );
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});

  await act(async () => {
    await result.current.keepDiscussionCard("d-1", "C1", "seed");
  });

  expect(result.current.roundInput).toContain("导出先只做全量，筛选等有人骂再加");
  expect(result.current.pendingSeeds).toEqual(["discussion:d-1#C1"]);
});

it("收种子失败：输入框与 pendingSeeds 原样不动（毒种子进不来）", async () => {
  keepDiscussionCard.mockRejectedValueOnce(
    new ApiError("invariant:T5", "卡片 C1 已经是「已忽略」，不能再收下"),
  );
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});

  await act(async () => {
    await result.current.keepDiscussionCard("d-1", "C1", "seed");
  });

  expect(result.current.roundInput).toBe("");
  expect(result.current.pendingSeeds).toEqual([]);
  expect(result.current.state.error).toContain("已忽略");
});

it("忽略成功：卡片 state=dismissed 替换进线程", async () => {
  dismissDiscussionCard.mockResolvedValue(
    afterOneSay({ cards: [card({ state: "dismissed" })] }),
  );
  const { result } = renderHook(() => useWorkspaceData());
  await act(async () => {});

  await act(async () => {
    await result.current.dismissDiscussionCard("d-1", "C1");
  });

  expect(result.current.discussions[0]?.cards[0]?.state).toBe("dismissed");
  expect(result.current.state.error).toBeNull();
});
