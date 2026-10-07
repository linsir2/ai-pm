/* 模拟评测纯函数（M11-T9）：默认勾选与上限。

   上限真相源在后端（`/schema.limits`），这里测的是"拿到/拿不到都算对"。 */

import { describe, expect, it } from "vitest";

import type { DocumentView, LimitsView, PersonaSchemaView } from "../api/types";
import {
  FALLBACK_SIM_REVIEW_LIMITS,
  canStartReview,
  defaultFeatureSlugs,
  defaultPersonaIds,
  documentFeatures,
  reviewLimits,
} from "./simReview";

const personas: PersonaSchemaView[] = [
  { persona_id: "persona.first_touch", title: "首次上手者", situation: "第一次用" },
  { persona_id: "persona.bulk_operator", title: "高频批量操作者", situation: "每天重复" },
  { persona_id: "persona.interrupted", title: "中途离开者", situation: "做到一半离开" },
  { persona_id: "persona.edge_maker", title: "异常状态制造者", situation: "故意走边界" },
  { persona_id: "persona.boundary_walker", title: "边界外使用者", situation: "低权限误入" },
];

function block(feature_slug: string | null, children: unknown[] = []) {
  return {
    block_id: `b-${feature_slug ?? "s"}`,
    kind: feature_slug ? "feature" : "section",
    entries: [],
    children,
    field: null,
    field_title: null,
    feature_slug,
    manually_edited: false,
    multiple: false,
  };
}

const document_ = {
  document_id: "d1",
  project_id: "p1",
  title: "订单中台",
  version_no: 2,
  sections: [
    {
      section_id: "s5",
      title: "功能需求",
      blocks: [block("order-export", [block("order-export")]), block("checkout")],
    },
    { section_id: "s1", title: "背景", blocks: [block(null)] },
  ],
  preference_entries: [],
} as unknown as DocumentView;

describe("reviewLimits", () => {
  it("拿不到 schema 时回落 3/2（与后端常量同值，不是第二个真相源）", () => {
    expect(reviewLimits(null)).toEqual(FALLBACK_SIM_REVIEW_LIMITS);
    expect(reviewLimits(undefined)).toEqual({ personas: 3, features: 2 });
  });

  it("拿得到时读 schema 的值", () => {
    const limits = {
      max_seat_rounds: 3,
      max_questionnaires: 2,
      max_open_items_per_batch: 3,
      max_sim_review_personas: 2,
      max_sim_review_features: 1,
    } as LimitsView;
    expect(reviewLimits(limits)).toEqual({ personas: 2, features: 1 });
  });
});

describe("默认勾选", () => {
  it("persona 默认前 3 个（表的顺序就是默认序）", () => {
    expect(defaultPersonaIds(personas, { personas: 3, features: 2 })).toEqual([
      "persona.first_touch",
      "persona.bulk_operator",
      "persona.interrupted",
    ]);
  });

  it("功能按文档序去重、取前 2 个", () => {
    expect(documentFeatures(document_)).toEqual(["order-export", "checkout"]);
    expect(defaultFeatureSlugs(document_, { personas: 3, features: 2 })).toEqual([
      "order-export",
      "checkout",
    ]);
  });

  it("空文档给空清单（按钮会被 canStartReview 挡住）", () => {
    expect(defaultFeatureSlugs(null, { personas: 3, features: 2 })).toEqual([]);
  });
});

describe("canStartReview", () => {
  const limits = { personas: 3, features: 2 };

  it("1–3 人、1–2 功能才放行", () => {
    expect(canStartReview(["a"], ["f"], limits)).toBe(true);
    expect(canStartReview(["a", "b", "c"], ["f1", "f2"], limits)).toBe(true);
  });

  it("0 人或超员、0 功能或超员都不放行", () => {
    expect(canStartReview([], ["f"], limits)).toBe(false);
    expect(canStartReview(["a", "b", "c", "d"], ["f"], limits)).toBe(false);
    expect(canStartReview(["a"], [], limits)).toBe(false);
    expect(canStartReview(["a"], ["f1", "f2", "f3"], limits)).toBe(false);
  });
});
