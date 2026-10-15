/* 模板编辑器的纯函数层（`06` §2.3 / §4）：组件不做业务判断，全问这里。
 *
 * 三条不能破的规矩都在这批用例里：
 *  ① 章节 id 不跟位置走（`s{max+1}`，不填补空缺）——id 是提示词里给用户看的稳定符号；
 *  ② `section_id` → `id` 的字段名转换只在这一处（GET 与 POST/PUT 不一致）；
 *  ③ 锚点是**结构性**事实：`pickedRoles` 算出来的是"还剩哪些 role 没被占"，
 *     组件据此置灰，而不是等到提交再报错。 */

import { describe, expect, it } from "vitest";

import type { TemplateSectionView, TemplateView } from "../api/types";
import {
  addSection,
  blankDraft,
  canPickRole,
  isAnchor,
  moveSection,
  nextSectionId,
  pickedRoles,
  toPayload,
  validateDraft,
} from "./draft";

function section(over: Partial<TemplateSectionView> = {}): TemplateSectionView {
  return {
    section_id: "s1",
    title: "背景",
    purpose: "这一章写清现在什么样、痛在哪",
    role: "plain",
    slots: [],
    ...over,
  };
}

const LIGHT_IDS = ["s1", "s2", "s4", "s5", "s11", "s13"];

/** 只要章节 id 的一份（编辑形状的最小构造——组件调用点传的就是 sections）。 */
function byIds(ids: string[]): TemplateSectionView[] {
  return ids.map((id) => section({ section_id: id }));
}

describe("nextSectionId：取最大值 +1，永不填补空缺", () => {
  it("light 那样跳号的一份，新章是 s14 而不是填补 s3", () => {
    expect(nextSectionId(byIds(LIGHT_IDS))).toBe("s14");
  });

  it("连续编号时就是下一个", () => {
    expect(nextSectionId(byIds(["s1", "s5"]))).toBe("s6");
  });

  it("一章没有时从 s1 起", () => {
    expect(nextSectionId([])).toBe("s1");
  });

  it("认得出非 s 前缀的 id（不把它当数字吞掉）", () => {
    expect(nextSectionId(byIds(["tpl-x", "s3"]))).toBe("s4");
  });
});

describe("toPayload：section_id → id，且不发 coverage", () => {
  it("换字段名，其余原样", () => {
    const payload = toPayload([
      section({
        section_id: "s5",
        title: "功能需求",
        role: "feature",
        min_entries: { count: 2, kind: "acceptance" },
        slots: [{ name: "goal", title: "目标", kind: "text" }],
      }),
    ]);

    expect(payload[0]!).toEqual({
      id: "s5",
      title: "功能需求",
      purpose: "这一章写清现在什么样、痛在哪",
      role: "feature",
      slots: [{ name: "goal", title: "目标", kind: "text" }],
      min_entries: { count: 2, kind: "acceptance" },
    });
    expect("section_id" in payload[0]!).toBe(false);
  });

  it("min_entries 缺省就不带这个键（省得后端读到 null）", () => {
    expect("min_entries" in toPayload([section()])[0]!).toBe(false);
  });
});

describe("pickedRoles / canPickRole：锚点是结构事实，不是提交时的报错", () => {
  const anchors = [
    section({ section_id: "s5", role: "feature" }),
    section({ section_id: "s11", role: "open_questions" }),
    section({ section_id: "s13", role: "version_history" }),
  ];

  it("三锚点齐了 → 没有还可选的锚点 role", () => {
    expect(pickedRoles(anchors).sort()).toEqual(
      ["feature", "open_questions", "version_history"].sort(),
    );
  });

  it("只占了一个 → 已占的就只有那一个（另外两个还能选走 canPickRole 问）", () => {
    expect(pickedRoles([anchors[0]!])).toEqual(["feature"]);
    expect(canPickRole([anchors[0]!], "open_questions")).toBe(true);
    expect(canPickRole([anchors[0]!], "feature")).toBe(false);
  });

  it("普通章不占锚点", () => {
    expect(pickedRoles([section({ role: "plain" }), section({ role: "steps" })])).toEqual([]);
  });

  it("canPickRole 按已占用的那份来答", () => {
    expect(canPickRole(anchors, "feature")).toBe(false);
    expect(canPickRole(anchors, "steps")).toBe(true);
  });
});

describe("isAnchor：锚点章不可删", () => {
  it("三个锚点 role 是 True，其余 False", () => {
    expect(isAnchor({ ...section(), role: "feature" })).toBe(true);
    expect(isAnchor({ ...section(), role: "version_history" })).toBe(true);
    expect(isAnchor({ ...section(), role: "backbone" })).toBe(false);
  });
});

describe("moveSection / addSection：不可变 + id 不跟位置走", () => {
  const list = [section({ section_id: "s1" }), section({ section_id: "s5" }), section({ section_id: "s11" })];

  it("上移一位，顺序变、原数组不动", () => {
    expect(moveSection(list, 1, -1).map((item) => item.section_id)).toEqual([
      "s5", "s1", "s11",
    ]);
    expect(list.map((item) => item.section_id)).toEqual(["s1", "s5", "s11"]);
  });

  it("已在头上 / 尾上时原样返回（不越界）", () => {
    expect(moveSection(list, 0, -1).map((i) => i.section_id)).toEqual(["s1", "s5", "s11"]);
    expect(moveSection(list, 2, 1).map((i) => i.section_id)).toEqual(["s1", "s5", "s11"]);
  });

  it("加一章：新 id 是最大值+1，其余原样", () => {
    const next = addSection(list);
    expect(next).toHaveLength(4);
    expect(next[3]!.section_id).toBe("s12");
    expect(next[3]!.role).toBe("plain");
    expect(next[3]!.title).toBe("");
    expect(list).toHaveLength(3);
  });
});

describe("validateDraft：只报 UI 级问题，不复刻后端锚点校验", () => {
  it("标题空着 → 报错", () => {
    expect(validateDraft("x", [section({ title: "  " })]).join()).toContain("标题不能空");
  });

  it("槽位名重复 → 报错（后端也会拒，先在界面上说）", () => {
    const withDup = section({
      section_id: "s8",
      slots: [
        { name: "goal", title: "甲", kind: "text" },
        { name: "goal", title: "乙", kind: "text" },
      ],
    });
    expect(validateDraft("x", [withDup]).join()).toContain("槽位名重复");
  });

  it("没问题 → 空数组", () => {
    expect(validateDraft("标题", [section()])).toEqual([]);
  });
});

describe("blankDraft：从零建 = 预置三锚点（不是空白列表）", () => {
  it("恰好三章，三个 role 各就各位，且本身能过校验", () => {
    const draft = blankDraft();

    expect(draft.map((item) => item.role).sort()).toEqual(
      ["feature", "open_questions", "version_history"].sort(),
    );
    expect(validateDraft("新模板", draft)).toEqual([]);
  });

  it("每章都有标题与说明（两处都是必填，空着没法提交）", () => {
    for (const item of blankDraft()) {
      expect(item.title.trim()).not.toBe("");
      expect(item.purpose.trim()).not.toBe("");
    }
  });
});

describe("编辑形状与 GET 返回的是同一份结构", () => {
  it("模板的 sections 可以直接当草稿用（前端不自造结构）", () => {
    const view: TemplateView = {
      template_id: "light",
      title: "轻量版",
      builtin: true,
      sections: [section({ section_id: "s5", role: "feature" })],
    };
    expect(view.sections).toHaveLength(1);
    expect(toPayload(view.sections)[0]!.id).toBe("s5");
  });
});
describe("锚点角色问后端，不在前端抄一份（`02` §11.0.70）", () => {
  it("装了 schema 就按 schema 的那份来算锚点", async () => {
    const { setSchema } = await import("../state/schema");
    setSchema({
      sections: [],
      feature_fields: [],
      limits: { max_seat_rounds: 3, max_questionnaires: 2, max_open_items_per_batch: 3 },
      discussion_roles: [],
      template_anchor_roles: ["feature", "decision"],   // 后端将来加了第四个锚点
    });
    try {
      // 前端自己那份没有 decision，所以这里能变——这就是"真相源在后端"的意思
      expect(isAnchor({ ...section(), role: "decision" })).toBe(true);
      expect(isAnchor({ ...section(), role: "open_questions" })).toBe(false);
    } finally {
      setSchema(null);
    }
  });
});
