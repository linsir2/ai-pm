import { afterEach, describe, expect, it } from "vitest";

import type { SchemaView } from "../api/types";
import {
  FALLBACK_BLOCK_KINDS,
  FALLBACK_LIMITS,
  FALLBACK_TEMPLATE_ROLES,
  blockKinds,
  fieldTitle,
  limitsOf,
  sectionPurpose,
  setSchema,
  templateRoles,
} from "./schema";

const FROM_BACKEND: SchemaView = {
  sections: [
    {
      section_id: "s5",
      title: "功能需求",
      purpose: "每个功能一棵子树。",
      allows_features: true,
      slots: [],
    },
  ],
  feature_fields: [
    { name: "name", title: "名字", kind: "text", multiple: false, required: true },
  ],
  limits: { max_seat_rounds: 5, max_questionnaires: 4, max_open_items_per_batch: 9 },
  // 讨论角色（M9）：真相源在后端 roles.toml，这里照抄三行只为回落测试
  discussion_roles: [
    { role_id: "dlg.challenger", title: "反方", focus: "找漏洞、要反例、泼冷水" },
    { role_id: "dlg.user_voice", title: "用户之声", focus: "替真实用户说话" },
    { role_id: "dlg.analogist", title: "类比者", focus: "别人家怎么做过" },
  ],
};

afterEach(() => setSchema(null));

describe("schema", () => {
  it("拿到 /schema 就用后端那一份", () => {
    setSchema(FROM_BACKEND);

    expect(FROM_BACKEND.discussion_roles.map((item) => item.role_id)).toEqual([
      "dlg.challenger",
      "dlg.user_voice",
      "dlg.analogist",
    ]);
    expect(fieldTitle("name")).toBe("名字");
    expect(limitsOf().max_seat_rounds).toBe(5);
    expect(sectionPurpose("s5")).toBe("每个功能一棵子树。");
  });

  it("没拿到 /schema 就回落旧默认值，界面不空着", () => {
    setSchema(null);

    expect(fieldTitle("name")).toBe("名称");
    expect(limitsOf()).toEqual(FALLBACK_LIMITS);
    expect(sectionPurpose("s5")).toBeNull();
  });

  it("空字段就是空，不编", () => {
    expect(fieldTitle(null)).toBeNull();
  });
});

describe("模板枚举（`02` §11.0.70）", () => {
  afterEach(() => setSchema(null));

  it("角色下拉拿后端那份，回落到离线那一份", () => {
    expect(templateRoles()).toEqual(FALLBACK_TEMPLATE_ROLES);
    // 后端没给时也拿得到（老后端 / schema 还没到）
    expect(FALLBACK_TEMPLATE_ROLES).toContain("steps");
    expect(FALLBACK_TEMPLATE_ROLES).toContain("backbone");
    setSchema({ ...FROM_BACKEND, template_roles: ["feature", "plain"] });
    expect(templateRoles()).toEqual(["feature", "plain"]);
  });

  it("kind 下拉同理", () => {
    expect(blockKinds()).toEqual(FALLBACK_BLOCK_KINDS);
    expect(FALLBACK_BLOCK_KINDS).toContain("acceptance");
    setSchema({ ...FROM_BACKEND, block_kinds: ["text"] });
    expect(blockKinds()).toEqual(["text"]);
  });
});
