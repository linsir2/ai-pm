// @vitest-environment jsdom
/* 条目工具行（M13）：「讨论」把条目原话交给 DiscussionContext；没能力时不画。
   调研 / 改 / 删的既有行为不回归（同一行里的三个入口）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Entry } from "../api/types";
import { DiscussionScope } from "./DiscussionContext";
import { EditScope } from "./EditContext";
import { EntryList } from "./EntryList";
import { EvidenceScope } from "./EvidenceContext";
import { ExplorationScope } from "./ExplorationContext";

afterEach(cleanup);

const ENTRIES: Entry[] = [
  { entry_id: "e1", text: "导出要支持按客户名筛", refs: ["E1"] },
];

function draw({
  canDiscuss = true,
  canEdit = false,
  canResearch = false,
  onDiscuss = vi.fn(),
}: {
  canDiscuss?: boolean;
  canEdit?: boolean;
  canResearch?: boolean;
  onDiscuss?: (text: string) => void;
} = {}) {
  render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <EditScope
        value={{
          canEdit,
          busy: false,
          edit: () => undefined,
          add: () => undefined,
          addField: () => undefined,
          remove: () => undefined,
        }}
      >
        <ExplorationScope value={{ canStart: canResearch, start: () => undefined }}>
          <DiscussionScope value={{ canStart: canDiscuss, start: onDiscuss }}>
            <EntryList blockId="s1" entries={ENTRIES} />
          </DiscussionScope>
        </ExplorationScope>
      </EditScope>
    </EvidenceScope>,
  );
  return { onDiscuss };
}

describe("EntryList 的讨论入口", () => {
  it("点「讨论」把条目原话交出去（键盘/触屏的等价入口，D2 硬要求）", () => {
    const { onDiscuss } = draw();

    fireEvent.click(screen.getByRole("button", { name: "讨论这一条" }));

    expect(onDiscuss).toHaveBeenCalledWith("导出要支持按客户名筛");
  });

  it("没有能力时（没项目 / 有动作在跑）不画按钮", () => {
    draw({ canDiscuss: false });

    expect(screen.queryByRole("button", { name: "讨论这一条" })).toBeNull();
  });

  it("讨论与调研同门控、各交各的：两个按钮都在时互不干扰", () => {
    draw({ canDiscuss: true, canResearch: true });

    expect(screen.getByRole("button", { name: "讨论这一条" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "调研这一条" })).toBeTruthy();
  });
});
