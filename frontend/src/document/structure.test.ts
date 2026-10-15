import { describe, expect, it } from "vitest";

import type { BlockView, DocumentView } from "../api/types";
import {
  chapterAnchors,
  featureFieldTarget,
  featureHome,
  groupFields,
  listFeatures,
  resolveTarget,
  sectionOfEntry,
} from "./structure";

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

describe("sectionOfEntry", () => {
  it("功能字段里的条目也要能找到它所在的那一章", () => {
    const doc = {
      sections: [
        { section_id: "s1", title: "背景与问题", blocks: [] },
        {
          section_id: "s5",
          title: "功能需求",
          blocks: [
            block({
              block_id: "feat-order-export",
              kind: "feature",
              feature_slug: "order-export",
              children: [
                block({
                  block_id: "feat-order-export.acceptance",
                  kind: "acceptance",
                  field: "acceptance",
                  entries: [entry("en-ac1", "AC-1")],
                }),
              ],
            }),
          ],
        },
      ],
      preference_entries: [entry("en-pref", "偏好")],
    } as unknown as DocumentView;

    expect(sectionOfEntry(doc, "en-ac1")).toBe("s5");
    expect(sectionOfEntry(doc, "en-pref")).toBe("preference");
    expect(sectionOfEntry(doc, "不存在")).toBeNull();
    expect(sectionOfEntry(null, "en-ac1")).toBeNull();
  });
});

describe("resolveTarget（去处 → 文档落点；点去处跳转的唯一解析）", () => {
  const targetDoc = {
    sections: [
      { section_id: "s3", title: "用户与受众", blocks: [] },
      { section_id: "s5", title: "功能需求", blocks: [orderExport] },
      {
        section_id: "s8",
        title: "非功能需求",
        blocks: [
          block({ block_id: "b-s8-perf", kind: "text", field: "performance" }),
          block({ block_id: "b-s8-avail", kind: "text", field: "availability" }),
        ],
      },
    ],
    preference_entries: [],
  } as unknown as DocumentView;

  it("功能字段：落到那个字段的块（最精确）", () => {
    expect(resolveTarget(targetDoc, "s5/order-export.main_flow")).toEqual({
      sectionId: "s5",
      blockId: "b-flow",
    });
  });

  it("功能级：落到功能块", () => {
    expect(resolveTarget(targetDoc, "s5/order-export")).toEqual({
      sectionId: "s5",
      blockId: "b-feature-1",
    });
  });

  it("字段块不在时回落到功能块；功能也找不到回落到章（回落链不跳空）", () => {
    expect(resolveTarget(targetDoc, "s5/order-export.nope")).toEqual({
      sectionId: "s5",
      blockId: "b-feature-1",
    });
    expect(resolveTarget(targetDoc, "s5/nope.rules")).toEqual({
      sectionId: "s5",
      blockId: null,
    });
  });

  it("章节槽：落到槽块；槽还没建就落到章", () => {
    expect(resolveTarget(targetDoc, "s8.performance")).toEqual({
      sectionId: "s8",
      blockId: "b-s8-perf",
    });
    expect(resolveTarget(targetDoc, "s8.observability")).toEqual({
      sectionId: "s8",
      blockId: null,
    });
  });

  it("章级与偏好区", () => {
    expect(resolveTarget(targetDoc, "s3")).toEqual({ sectionId: "s3", blockId: null });
    expect(resolveTarget(targetDoc, "preference")).toEqual({
      sectionId: "preference",
      blockId: null,
    });
  });

  it("形状不认 / 章不在文档里 → null（调用方说实话，不假跳）", () => {
    expect(resolveTarget(targetDoc, "s5/")).toBeNull();
    expect(resolveTarget(targetDoc, "s99")).toBeNull();
    expect(resolveTarget(null, "s5")).toBeNull();
  });
});

// —— 功能章不再写死 s5（`02` §11.0.69）：找"含功能块的章" ——

describe("listFeatures · 功能锚点不是 s5 时", () => {
  const customHome = {
    document_id: "d1",
    project_id: "p1",
    title: "t",
    version_no: 1,
    preference_entries: [],
    feature_gaps: [],
    sections: [
      { section_id: "feat", title: "功能", blocks: [orderExport], role: "feature" },
      { section_id: "open", title: "未决", blocks: [], role: "open_questions" },
    ],
  } as unknown as DocumentView;

  it("功能章 id 是 feat 也照样找得到", () => {
    expect(listFeatures(customHome).map((item) => item.slug)).toEqual(["order-export"]);
  });

  it("featureHome 返回那一章的 id", () => {
    expect(featureHome(customHome)).toBe("feat");
  });

  it("没有功能块时 featureHome 为 null（不猜）", () => {
    expect(featureHome(null)).toBeNull();
  });
});

describe("featureFieldTarget · 手改功能字段写进锚点章", () => {
  it("写进模板的功能锚点，不是 s5", () => {
    const doc = {
      document_id: "d1",
      project_id: "p1",
      title: "t",
      version_no: 1,
      preference_entries: [],
      feature_gaps: [],
      sections: [
        { section_id: "feat", title: "功能", blocks: [orderExport], role: "feature" },
      ],
    } as unknown as DocumentView;

    expect(featureFieldTarget(doc, "order-export", "one_liner")).toEqual({
      zone: "body",
      section_id: "feat",
      feature_slug: "order-export",
      field: "one_liner",
    });
  });
});
