// @vitest-environment jsdom
/* 记忆抽屉（M6）：分组、出处胶囊、删除；空态说人话。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MemoryEntryView } from "../api/types";
import { EvidenceScope } from "./EvidenceContext";
import { MemoryDrawer } from "./MemoryDrawer";

afterEach(cleanup);

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
});
