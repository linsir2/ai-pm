import { describe, expect, it } from "vitest";

import type { BlockView, DocumentView } from "../api/types";
import { chapterAnchors, groupFields, listFeatures } from "./structure";

function entry(id: string, text: string) {
  return { entry_id: id, text, refs: ["E1"] };
}

function block(partial: Partial<BlockView> & { block_id: string; kind: string }): BlockView {
  return {
    entries: [],
    children: [],
    field: null,
    field_title: null,
    feature_slug: null,
    manually_edited: false,
    multiple: true,
    ...partial,
  };
}

const orderExport = block({
  block_id: "b-feature-1",
  kind: "feature",
  feature_slug: "order-export",
  children: [
    block({ block_id: "b-name", kind: "text", field: "name", entries: [entry("e1", "订单导出功能")] }),
    block({ block_id: "b-one", kind: "text", field: "one_liner", entries: [entry("e2", "按时间筛选")] }),
    block({ block_id: "b-flow", kind: "steps", field: "main_flow", entries: [entry("e3", "进页面")] }),
    block({ block_id: "b-acc", kind: "acceptance", field: "acceptance", entries: [entry("e4", "AC1")] }),
  ],
});

const doc = {
  sections: [
    { section_id: "s1", title: "背景与问题", blocks: [] },
    { section_id: "s5", title: "功能需求", blocks: [orderExport] },
  ],
} as unknown as DocumentView;

describe("listFeatures", () => {
  it("只从 s5 里找功能子树，每条带上名称与一句话描述", () => {
    const features = listFeatures(doc);
    expect(features).toHaveLength(1);
    expect(features[0]!.slug).toBe("order-export");
    expect(features[0]!.name).toBe("订单导出功能");
    expect(features[0]!.oneLiner).toBe("按时间筛选");
  });

  it("没有文档就没有功能", () => {
    expect(listFeatures(null)).toEqual([]);
  });

  it("名称还没写时用 slug 顶着，不编内容", () => {
    const bare = {
      sections: [
        { section_id: "s5", title: "功能需求", blocks: [block({ block_id: "b9", kind: "feature", feature_slug: "login-ui" })] },
      ],
    } as unknown as DocumentView;
    expect(listFeatures(bare)[0]!.name).toBe("login-ui");
    expect(listFeatures(bare)[0]!.oneLiner).toBeNull();
  });
});

describe("groupFields", () => {
  it("整段文字与步骤进正文，清单与验收标准进并列网格", () => {
    const group = groupFields(orderExport);
    expect(group.sections.map((item) => item.block_id)).toEqual(["b-name", "b-one", "b-flow"]);
    expect(group.grid.map((item) => item.block_id)).toEqual(["b-acc"]);
  });
});

describe("chapterAnchors", () => {
  it("页内锚点 = 功能 + 它的字段（用中文名）", () => {
    const anchors = chapterAnchors(doc, "s5");
    expect(anchors.map((anchor) => anchor.text)).toEqual([
      "order-export",
      "名称",
      "一句话描述",
      "主流程",
      "验收标准",
    ]);
    expect(anchors[0]!.id).toBe("block-b-feature-1");
  });

  it("没有功能的章节没有页内锚点", () => {
    expect(chapterAnchors(doc, "s1")).toEqual([]);
  });
});
