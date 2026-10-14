/* label 形状的唯一解析器（镜像后端 `target_from_label`）+ 槽标签显示修复。
   RIPR：fault = "s8.performance" 这类槽去处在界面上显示生串/跳不动；
   R = 调 parseTarget/targetText，I = 形状走形，P = 生串漏到输出，R = 断言人话与 null。 */

import { afterEach, describe, expect, it } from "vitest";

import type { SchemaView } from "./api/types";
import { parseTarget, targetText } from "./labels";
import { setSchema } from "./state/schema";

const schema = {
  sections: [
    { section_id: "s5", title: "功能需求", purpose: "", allows_features: true, slots: [] },
    {
      section_id: "s8",
      title: "非功能需求",
      purpose: "",
      allows_features: false,
      slots: [{ name: "performance", title: "性能", kind: "text" }],
    },
  ],
  feature_fields: [
    { name: "rules", title: "状态与规则", kind: "text", multiple: true, required: false },
  ],
  limits: {},
  discussion_roles: [],
} as unknown as SchemaView;

afterEach(() => setSchema(null));

describe("parseTarget（和后端 target_from_label 同一个形状表）", () => {
  it("五种合法形态", () => {
    expect(parseTarget("preference")).toEqual({ zone: "preference" });
    expect(parseTarget("s8.performance")).toEqual({
      zone: "body",
      sectionId: "s8",
      slug: null,
      field: "performance",
    });
    expect(parseTarget("s5")).toEqual({ zone: "body", sectionId: "s5", slug: null, field: null });
    expect(parseTarget("s5/order-export")).toEqual({
      zone: "body",
      sectionId: "s5",
      slug: "order-export",
      field: null,
    });
    expect(parseTarget("s5/order-export.rules")).toEqual({
      zone: "body",
      sectionId: "s5",
      slug: "order-export",
      field: "rules",
    });
  });

  it("残缺形态一律 null（形状不认就不猜）", () => {
    expect(parseTarget("")).toBeNull();
    expect(parseTarget("   ")).toBeNull();
    expect(parseTarget("s5/")).toBeNull();
    expect(parseTarget("s5/order-export.")).toBeNull();
    expect(parseTarget("s5.")).toBeNull();
    expect(parseTarget(".performance")).toBeNull();
  });
});

describe("targetText 认槽标签（防生串 s8.performance 漏给用户）", () => {
  it("槽字段用 /schema 的中文名，章节带标题", () => {
    setSchema(schema);
    const sections = [{ section_id: "s8", title: "非功能需求" }] as never;
    expect(targetText("s8.performance", sections)).toBe("s8 非功能需求 · 性能");
  });

  it("功能字段仍走原形态（斜杠 + 点号）", () => {
    setSchema(schema);
    const sections = [{ section_id: "s5", title: "功能需求" }] as never;
    expect(targetText("s5/order-export.rules", sections)).toBe(
      "s5 功能需求 · order-export · 状态与规则",
    );
  });

  it("认不出的槽字段照实显示原值（不编）", () => {
    setSchema(schema);
    expect(targetText("s4.unknown")).toBe("s4 · unknown");
  });

  it("偏好区不变", () => {
    expect(targetText("preference")).toBe("偏好区");
  });
});
