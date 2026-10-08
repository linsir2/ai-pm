/* 讨论区的纯函数（M9/M12）：角色解析、卡的过目动作与状态、追问门控。
   组件不许做这些判断——它只按这里的结果画。 */

import { describe, expect, it } from "vitest";

import type { CardView, QuestionView, SchemaView, ThreadView } from "../api/types";
import {
  canDiscuss,
  cardActions,
  cardSeedText,
  cardStatusText,
  resolveRoleId,
  roleChoices,
  roleOptionOf,
  roleOptions,
} from "./discussions";

const FROM_SCHEMA = {
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
  limits: { max_seat_rounds: 3, max_questionnaires: 2, max_open_items_per_batch: 3 },
  discussion_roles: [
    { role_id: "dlg.challenger", title: "反方", focus: "找漏洞" },
    { role_id: "dlg.user_voice", title: "用户之声", focus: "替用户说话" },
    { role_id: "dlg.analogist", title: "类比者", focus: "别人家怎么做过" },
  ],
} as SchemaView;

function turn(index: number, role: string) {
  return { index, role, text: `第${index}轮${role}`, based_on: [] };
}

function thread(over: Partial<ThreadView> = {}): ThreadView {
  return {
    thread_id: "d-1",
    project_id: "p1",
    topic: "导出要不要做筛选",
    question: null,
    role_id: "dlg.challenger",
    role_title: "反方",
    status: "open",
    turns: [],
    cards: [],
    created_at: "2026-10-05T10:00:00+00:00",
    ...over,
  };
}

function card(over: Partial<CardView> = {}): CardView {
  return {
    card_id: "C1",
    statement: "导出先只做全量，筛选等有人骂再加",
    grounding: "invented",
    based_on: [],
    kept_ref: null,
    state: "new",
    kept_as: null,
    ...over,
  };
}

function question(over: Partial<QuestionView> = {}): QuestionView {
  return {
    question_id: "q1",
    kind: "gap",
    type: "single",
    title: "保存几年？",
    why: "合规要求",
    target_label: "s8",
    options: [],
    consequence_if_skipped: "按三年实现",
    eids: [],
    asked_by: [],
    blocking: false,
    ...over,
  };
}

describe("resolveRoleId", () => {
  it("显式给的赢（问卷是谁问的不抢）", () => {
    expect(
      resolveRoleId("dlg.analogist", question({ asked_by: ["dlg.challenger"] })),
    ).toBe("dlg.analogist");
  });

  it("没显式给就用问卷题的 asked_by[0]（席位 id 照收，后端认 seat.*）", () => {
    expect(resolveRoleId(undefined, question({ asked_by: ["seat.data_scale"] }))).toBe(
      "seat.data_scale",
    );
  });

  it("两边都没有就默认反方", () => {
    expect(resolveRoleId()).toBe("dlg.challenger");
    expect(resolveRoleId(null, null)).toBe("dlg.challenger");
  });
});

describe("roleOptions", () => {
  it("/schema 给的三行就是全部（照抄，不排序不补）", () => {
    expect(roleOptions(FROM_SCHEMA).map((item) => item.role_id)).toEqual([
      "dlg.challenger",
      "dlg.user_voice",
      "dlg.analogist",
    ]);
  });

  it("拿不到 /schema 就只回落默认那一个（后端 role_id 必填，回落空了连默认都发不出去）", () => {
    expect(roleOptions(null)).toEqual([
      { role_id: "dlg.challenger", title: "反方", focus: "找漏洞、要反例、泼冷水" },
    ]);
  });
});

describe("roleOptionOf", () => {
  it("表里有就取那一条", () => {
    expect(roleOptionOf("dlg.analogist", FROM_SCHEMA.discussion_roles).title).toBe("类比者");
  });

  it("表里没有就照实显示原值（问卷带进来的 seat.* 就是这条路径，不猜中文名）", () => {
    expect(roleOptionOf("seat.data_scale", FROM_SCHEMA.discussion_roles)).toEqual({
      role_id: "seat.data_scale",
      title: "seat.data_scale",
      focus: "",
    });
  });
});

describe("roleChoices", () => {
  it("锚点那条排最前（问卷入口 UX）", () => {
    expect(roleChoices("dlg.analogist", FROM_SCHEMA.discussion_roles).map((i) => i.role_id)).toEqual([
      "dlg.challenger",
      "dlg.user_voice",
      "dlg.analogist",
    ]);
    expect(
      roleChoices("seat.data_scale", FROM_SCHEMA.discussion_roles).map((i) => i.role_id),
    ).toEqual(["seat.data_scale", "dlg.challenger", "dlg.user_voice", "dlg.analogist"]);
  });
});

describe("cardActions", () => {
  it("新卡三条路都通：收种子 / 收材料 / 忽略", () => {
    expect(cardActions(card())).toEqual(["keep-seed", "keep-material", "dismiss"]);
  });

  it("收下过的卡（种子或材料）不再给动作", () => {
    expect(cardActions(card({ state: "kept", kept_as: "seed" }))).toEqual([]);
    expect(cardActions(card({ state: "kept", kept_as: "material", kept_ref: "m-1" })))
      .toEqual([]);
  });

  it("忽略过的卡不再给动作", () => {
    expect(cardActions(card({ state: "dismissed" }))).toEqual([]);
  });

  it("关掉的线程也能收：收下是回流不是聊天（后端 keep_card 不判 status）", () => {
    const closed = thread({ status: "closed", cards: [card()] });
    const kept = closed.cards.at(0);

    expect(kept).toBeDefined();
    expect(cardActions(kept!)).toEqual(["keep-seed", "keep-material", "dismiss"]);
  });
});

describe("cardStatusText", () => {
  it("新卡没有状态文案（状态由动作按钮表达）", () => {
    expect(cardStatusText(card())).toBeNull();
  });

  it("三种已处理状态各说各的话", () => {
    expect(cardStatusText(card({ state: "kept", kept_as: "seed" }))).toBe(
      "已收下为下一轮输入",
    );
    expect(
      cardStatusText(card({ state: "kept", kept_as: "material", kept_ref: "m-1" })),
    ).toBe("已收下为材料");
    expect(cardStatusText(card({ state: "dismissed" }))).toBe("已忽略");
  });
});

describe("cardSeedText", () => {
  it("收下为下一轮输入时预填的就是卡片原话", () => {
    expect(cardSeedText(card({ statement: "按客户名筛最直观" }))).toBe(
      "按客户名筛最直观",
    );
  });
});

describe("canDiscuss", () => {
  it("open 且下一说还塞得进一对就能追问", () => {
    expect(canDiscuss(thread())).toBe(true);
    expect(
      canDiscuss(thread({ turns: Array.from({ length: 10 }, (_, i) => turn(i, "user")) })),
    ).toBe(true);
  });

  it("只剩 11 轮时不能再追问（一说就是一**对**，塞不下就是 T4 拒）", () => {
    // 后端口径是 `len(turns) + 2 > 12` 才拒（`thread.py:149`）：11 轮时再说要凑到 13 > 12。
    // 界面门必须与它同口径，否则会点亮一个必然被拒的按钮。
    expect(
      canDiscuss(thread({ turns: Array.from({ length: 11 }, (_, i) => turn(i, "user")) })),
    ).toBe(false);
    expect(
      canDiscuss(thread({ turns: Array.from({ length: 12 }, (_, i) => turn(i, "user")) })),
    ).toBe(false);
  });

  it("关掉的线程不能追问", () => {
    expect(canDiscuss(thread({ status: "closed" }))).toBe(false);
  });
});
