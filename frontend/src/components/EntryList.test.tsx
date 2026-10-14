// @vitest-environment jsdom
/* 条目工具行（M17）：讨论/调研入口已收口到选区浮条，工具行只剩改/删。
   旧的"条目行等价入口"（D2）随 §11.0.51 改写一并退休。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

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

function draw({ canEdit = true }: { canEdit?: boolean } = {}) {
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
        <ExplorationScope value={{ canStart: true, start: () => undefined }}>
          <DiscussionScope value={{ canStart: true, start: () => undefined }}>
            <EntryList blockId="s1" entries={ENTRIES} />
          </DiscussionScope>
        </ExplorationScope>
      </EditScope>
    </EvidenceScope>,
  );
}

describe("EntryList 的工具行（M17：讨论/调研已收口到选区浮条）", () => {
  it("E1 工具行里没有「讨论」「调研」，只剩改/删", () => {
    draw();

    expect(screen.queryByRole("button", { name: "讨论这一条" })).toBeNull();
    expect(screen.queryByRole("button", { name: "调研这一条" })).toBeNull();
    expect(screen.getByRole("button", { name: "改这一条" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "删这一条" })).toBeTruthy();
  });

  it("E2 没能力（canEdit=false）时工具行整个不出", () => {
    draw({ canEdit: false });

    expect(screen.queryByRole("button", { name: "改这一条" })).toBeNull();
    expect(screen.queryByRole("button", { name: "删这一条" })).toBeNull();
  });

  it("E3 改按钮照常进编辑态（收口没动改/删）", () => {
    draw();

    fireEvent.click(screen.getByRole("button", { name: "改这一条" }));

    expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
  });
});
