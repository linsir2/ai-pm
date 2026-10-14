/* 讨论区的纯函数（M9/M12）：角色解析、卡的过目动作与状态、追问门控。
   组件不许做这些判断——它只按这里的结果画。 */

import { describe, expect, it } from "vitest";

import type { CardView, SchemaView, ThreadView } from "../api/types";
import { FALLBACK_LIMITS } from "./schema";
import {
  canDiscuss,
  cardActions,
  cardSeedText,
  cardStatusText,
  discussionTopic,
  discussionTurnLimit,
  FALLBACK_DISCUSSION_TURNS,
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
  return { index, role, text: `第${index}轮${role}`, based_on: [], author: role, round: 1 };
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
    findings_count: 0,
    created_at: "2026-10-05T10:00:00+00:00",
    participants: [],
    summaries: [],
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
    expect(canDiscuss(thread(), 12)).toBe(true);
    expect(
      canDiscuss(thread({ turns: Array.from({ length: 10 }, (_, i) => turn(i, "user")) }), 12),
    ).toBe(true);
  });

  it("只剩 11 轮时不能再追问（一说就是一**对**，塞不下就是 T4 拒）", () => {
    // 后端口径是 `len(turns) + 2 > 上限` 才拒（`thread.py`）：11 轮时再说要凑到 13。
    // 界面门必须与它同口径，否则会点亮一个必然被拒的按钮。
    expect(
      canDiscuss(thread({ turns: Array.from({ length: 11 }, (_, i) => turn(i, "user")) }), 12),
    ).toBe(false);
    expect(
      canDiscuss(thread({ turns: Array.from({ length: 12 }, (_, i) => turn(i, "user")) }), 12),
    ).toBe(false);
  });

  it("上限是参数：同一个线程换上限，门跟着变（M13 起真相源在 /schema.limits）", () => {
    const six = thread({ turns: Array.from({ length: 6 }, (_, i) => turn(i, "user")) });

    expect(canDiscuss(six, 8)).toBe(true); // 6 + 2 = 8，塞得下
    expect(canDiscuss(six, 6)).toBe(false); // 6 + 2 > 6，塞不下
  });

  it("关掉的线程不能追问", () => {
    expect(canDiscuss(thread({ status: "closed" }), 12)).toBe(false);
  });
});

describe("discussionTurnLimit", () => {
  it("有 schema.limits 就用后端值", () => {
    expect(discussionTurnLimit({ ...FALLBACK_LIMITS, max_discussion_turns: 16 })).toBe(16);
  });

  it("拿不到就回落（回落是同一份值的离线抄本，不是第二真相源）", () => {
    expect(discussionTurnLimit(null)).toBe(FALLBACK_DISCUSSION_TURNS);
    expect(discussionTurnLimit(undefined)).toBe(FALLBACK_DISCUSSION_TURNS);
  });
});

describe("discussionTopic", () => {
  it("归一化空白（与后端 join(split()) 同式）", () => {
    expect(discussionTopic("  导出   要不要按 客户名筛  ")).toBe("导出 要不要按 客户名筛");
  });

  it("超长截到 200 且以省略号收尾（后端 topic max_length=200）", () => {
    const long = "啊".repeat(260);
    const topic = discussionTopic(long);

    expect(topic.length).toBe(200);
    expect(topic.endsWith("…")).toBe(true);
  });

  it("恰好 200 不动刀", () => {
    const exact = "啊".repeat(200);
    expect(discussionTopic(exact)).toBe(exact);
  });
});

describe("validatePanel（M18 开跑校验：组件只显示，不定规则）", () => {
  it("空名单、重复请人、超 4 人都给人话", async () => {
    const { validatePanel } = await import("./discussions");

    expect(validatePanel([], 3)).toBe("至少请一个人");
    expect(validatePanel(["dlg.challenger", "dlg.challenger"], 3)).toBe(
      "同一个角色不能请两次",
    );
    expect(
      validatePanel(["a", "b", "c", "d", "e"], 3),
    ).toBe("一次最多请 4 个人");
  });

  it("轮数只能 1–5 的整数", async () => {
    const { validatePanel } = await import("./discussions");

    expect(validatePanel(["dlg.challenger"], 0)).toBe("轮数只能是 1–5");
    expect(validatePanel(["dlg.challenger"], 6)).toBe("轮数只能是 1–5");
    expect(validatePanel(["dlg.challenger"], 1.5)).toBe("轮数只能是 1–5");
  });

  it("合法返回 null", async () => {
    const { validatePanel } = await import("./discussions");

    expect(validatePanel(["dlg.challenger"], 1)).toBeNull();
    expect(validatePanel(["a", "b", "c", "d"], 5)).toBeNull();
  });
});

describe("joinableRoles（M18：已在场的排除）", () => {
  it("在场的不再给", async () => {
    const { joinableRoles } = await import("./discussions");
    const options = [
      { role_id: "dlg.challenger", title: "反方", focus: "" },
      { role_id: "dlg.user_voice", title: "用户之声", focus: "" },
    ];

    expect(joinableRoles(options, thread())).toEqual(options);
    expect(
      joinableRoles(
        options,
        thread({
          participants: [
            { role_id: "dlg.challenger", role_title: "反方", joined_round: 1 },
          ],
        }),
      ),
    ).toEqual([{ role_id: "dlg.user_voice", title: "用户之声", focus: "" }]);
  });
});

describe("authorTitle（M18：发言人显示名）", () => {
  it("你 / 在场成员标题 / 陌生 id 照实显示", async () => {
    const { authorTitle } = await import("./discussions");
    const paneled = thread({
      participants: [{ role_id: "dlg.challenger", role_title: "反方", joined_round: 1 }],
    });

    expect(authorTitle(paneled, "user")).toBe("你");
    expect(authorTitle(paneled, "dlg.challenger")).toBe("反方");
    expect(authorTitle(paneled, "dlg.nobody")).toBe("dlg.nobody");
  });
});

describe("roundsOf（M18：副栏按轮分组）", () => {
  it("按 round 分组排序", async () => {
    const { roundsOf } = await import("./discussions");

    expect(roundsOf(thread())).toEqual([]);
    expect(
      roundsOf(
        thread({
          turns: [
            { index: 0, role: "assistant", text: "a", based_on: [], author: "x", round: 2 },
            { index: 1, role: "assistant", text: "b", based_on: [], author: "y", round: 1 },
          ],
        }),
      ),
    ).toEqual([
      {
        round: 1,
        turns: [{ index: 1, role: "assistant", text: "b", based_on: [], author: "y", round: 1 }],
      },
      {
        round: 2,
        turns: [{ index: 0, role: "assistant", text: "a", based_on: [], author: "x", round: 2 }],
      },
    ]);
  });

  it("有综合但没 turns 的轮次也要画出来（不许静默漏一组）", async () => {
    const { roundsOf } = await import("./discussions");

    expect(
      roundsOf(
        thread({
          summaries: [{ index: 1, agreed: ["共识"], disagreed: [], open: [] }],
        }),
      ),
    ).toEqual([{ round: 1, turns: [] }]);
  });
});

describe("defaultPanelRoles（M18：开跑默认勾选）", () => {
  it("默认勾上全部固定角色（开箱即灵感 panel）", async () => {
    const { defaultPanelRoles } = await import("./discussions");
    const options = [
      { role_id: "dlg.challenger", title: "反方", focus: "" },
      { role_id: "dlg.user_voice", title: "用户之声", focus: "" },
      { role_id: "dlg.analogist", title: "类比者", focus: "" },
    ];

    expect(defaultPanelRoles(null, options)).toEqual([
      "dlg.challenger",
      "dlg.user_voice",
      "dlg.analogist",
    ]);
  });

  it("问卷带来的表外席位也默认勾上（M13 预选不断），且不超 4 个", async () => {
    const { defaultPanelRoles } = await import("./discussions");
    const options = [
      { role_id: "seat.data_scale", title: "席位", focus: "" },
      { role_id: "dlg.challenger", title: "反方", focus: "" },
      { role_id: "dlg.user_voice", title: "用户之声", focus: "" },
      { role_id: "dlg.analogist", title: "类比者", focus: "" },
    ];

    expect(defaultPanelRoles("seat.data_scale", options)).toEqual([
      "seat.data_scale",
      "dlg.challenger",
      "dlg.user_voice",
      "dlg.analogist",
    ]);
  });
});
