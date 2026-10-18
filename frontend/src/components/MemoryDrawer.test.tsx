// @vitest-environment jsdom
/* 记忆抽屉（M6）：分组、出处胶囊、删除；空态说人话。 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MemoryEntryView } from "../api/types";
import { EvidenceScope } from "./EvidenceContext";
import { MemoryDrawer } from "./MemoryDrawer";

afterEach(cleanup);

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

/** 按 URL 后缀给回包，并记下每一次 fetch（断言"懒查只打一次"用）。 */
function stubFetch(routes: Record<string, unknown>): string[] {
  const calls: string[] = [];
  globalThis.fetch = (async (url: unknown) => {
    const path = String(url);
    calls.push(path);
    const key = Object.keys(routes).find((suffix) => path.endsWith(suffix));
    const body = key === undefined ? {} : routes[key];
    return {
      ok: true,
      status: 200,
      json: async () => body,
      text: async () => JSON.stringify(body),
    };
  }) as unknown as typeof fetch;
  return calls;
}

function drawUnused(over: Partial<Parameters<typeof MemoryDrawer>[0]> = {}) {
  const props = {
    projectId: "p1",
    memory: entries,
    pending: false,
    onDelete: vi.fn(),
    onCollapse: vi.fn(),
    ...over,
  };
  render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <MemoryDrawer {...props} />
    </EvidenceScope>,
  );
  return props;
}

const entries: MemoryEntryView[] = [
  {
    memory_id: "mem1",
    kind: "decision",
    text: "导出保留期是 30 天",
    refs: ["E1"],
    round_id: "r1",
    updated_at: "2026-10-03T20:00:00+00:00",
  },
  {
    memory_id: "mem2",
    kind: "lesson",
    text: "席位给判断必须带出处编号",
    refs: ["E7"],
    round_id: "r2",
    updated_at: "2026-10-03T21:00:00+00:00",
  },
];

function draw(over: Partial<Parameters<typeof MemoryDrawer>[0]> = {}) {
  const props = {
    memory: entries,
    pending: false,
    onDelete: vi.fn(),
    onCollapse: vi.fn(),
    ...over,
  };
  render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <MemoryDrawer {...props} />
    </EvidenceScope>,
  );
  return props;
}

describe("MemoryDrawer", () => {
  it("按类别分组，并说清删了会怎样", () => {
    draw();

    expect(screen.getByText(/记忆 · 2 条/)).toBeTruthy();
    expect(screen.getByText("已定口径")).toBeTruthy();
    expect(screen.getByText("教训")).toBeTruthy();
    expect(screen.getByText("导出保留期是 30 天")).toBeTruthy();
    expect(screen.getByText(/删掉一条，下一轮就不再看到它/)).toBeTruthy();
  });

  it("点『删掉这条』把那条的 id 交出去", () => {
    const props = draw();

    fireEvent.click(screen.getAllByText("删掉这条")[0] as HTMLElement);

    expect(props.onDelete).toHaveBeenCalledWith("mem1");
  });

  it("每条是单子元素的 .prop（52px 两列网格只给有侧栏的卡用，L23）", () => {
    const { container } = render(
      <EvidenceScope index={new Map()} onJump={() => undefined}>
        <MemoryDrawer memory={entries} pending={false} onDelete={() => undefined} onCollapse={() => undefined} />
      </EvidenceScope>,
    );

    const articles = container.querySelectorAll("article.prop");
    expect(articles.length).toBe(2);
    for (const article of Array.from(articles)) {
      // 单一子元素 → 命中 `.prop > :only-child { grid-column: 1/-1 }`，正文才占满整行
      expect(article.children.length).toBe(1);
    }
  });

  it("没有记忆时说人话，不留空白", () => {
    draw({ memory: [] });

    expect(screen.getByText(/还没有确认过的记忆/)).toBeTruthy();
  });

  it("命中为 0 的记忆在抽屉里有「久未使用」标签，删除入口照旧", async () => {
    stubFetch({ "/projects/p1/memory/usage": { window: 10, unused: ["mem1"] } });
    drawUnused({ memory: [entries[0] as MemoryEntryView] });

    expect(await screen.findByText("久未使用")).toBeTruthy();
    const del = screen.getByRole("button", { name: "删掉这条" });
    expect(del).toBeTruthy();
    expect(del.getAttribute("disabled")).toBeNull();
  });

  it("被引用过的记忆不带那个标签", async () => {
    const calls = stubFetch({ "/projects/p1/memory/usage": { window: 10, unused: [] } });
    drawUnused({ memory: [entries[0] as MemoryEntryView] });

    // 先等懒查落定再断言"没有"——否则是"还没查回来"，不是"真没有"。
    await waitFor(() => expect(calls.length).toBe(1));
    expect(screen.queryByText("久未使用")).toBeNull();
  });

  it("usage 请求失败时静默降级：不打标，抽屉照常", async () => {
    const calls: string[] = [];
    globalThis.fetch = (async (url: unknown) => {
      calls.push(String(url));
      throw new Error("断网");
    }) as unknown as typeof fetch;
    drawUnused({ memory: [entries[0] as MemoryEntryView] });

    await waitFor(() => expect(calls.length).toBe(1));
    expect(screen.queryByText("久未使用")).toBeNull();
    expect(screen.getByText("导出保留期是 30 天")).toBeTruthy();
  });
});
