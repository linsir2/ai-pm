/* 灵感箱的视图计算（M12）：箱 = 两个既有投影（线程卡 + 调研候选）的合并视图。
   不存数据、不发请求——数字与列表都从后端真身现算，组件不许自己判断。 */

import { describe, expect, it } from "vitest";

import type { CardView, CandidateView, ExplorationView, ThreadView } from "../api/types";
import { inboxCards, inboxCount } from "./inbox";

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

function thread(over: Partial<ThreadView> = {}): ThreadView {
  return {
    thread_id: "d-1",
    project_id: "p1",
    topic: "导出要不要加筛选",
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

function candidate(over: Partial<CandidateView> = {}): CandidateView {
  return {
    candidate_id: "H1",
    kind: "hypothesis",
    statement: "导出要支持按状态筛选",
    state: "new",
    kept_as: null,
    kept_ref: null,
    grounding: "document",
    based_on: ["E1"],
    falsification: "如果用户只导全量，就不需要筛选",
    evidence_needed: null,
    next_step: null,
    ...over,
  };
}

function exploration(over: Partial<ExplorationView> = {}): ExplorationView {
  return {
    exploration_id: "x-1",
    project_id: "p1",
    kind: "inquiry",
    status: "done",
    scope_kind: "document",
    scope_ref: null,
    scope_text: null,
    created_at: "2026-10-05T10:00:00+00:00",
    failure_reason: null,
    abandon_reason: null,
    overflow_count: 0,
    candidates: [candidate()],
    ...over,
  };
}

describe("inboxCards", () => {
  it("只收 state=new 的卡，按线程与卡的原顺序，并带上所属线程", () => {
    const first = thread({ thread_id: "d-1", cards: [card({ card_id: "C1" })] });
    const second = thread({
      thread_id: "d-2",
      cards: [
        card({ card_id: "C1", state: "kept", kept_as: "seed" }),
        card({ card_id: "C2" }),
      ],
    });

    const items = inboxCards([first, second]);

    expect(items.map((item) => [item.thread.thread_id, item.card.card_id])).toEqual([
      ["d-1", "C1"],
      ["d-2", "C2"],
    ]);
    expect(items[0]!.thread.topic).toBe("导出要不要加筛选");
  });

  it("没有线程 / 没有新卡 → 空（0 条不是渲染问题，是不存在）", () => {
    expect(inboxCards([])).toEqual([]);
    expect(inboxCards([thread({ cards: [card({ state: "dismissed" })] })])).toEqual([]);
  });
});

describe("inboxCount", () => {
  it("新卡 + 待过目候选一起数", () => {
    const items = inboxCount([thread({ cards: [card(), card({ card_id: "C2" })] })], [
      exploration({ candidates: [candidate(), candidate({ candidate_id: "B1" })] }),
    ]);

    expect(items).toBe(4);
  });

  it("已处理的不算：收下/忽略的卡、已收/已删的候选都不进数", () => {
    const items = inboxCount(
      [
        thread({
          cards: [
            card({ state: "kept", kept_as: "seed" }),
            card({ card_id: "C2", state: "dismissed" }),
          ],
        }),
      ],
      [
        exploration({
          candidates: [
            candidate({ state: "kept", kept_as: "material", kept_ref: "m-1" }),
            candidate({ candidate_id: "B1", state: "dismissed" }),
          ],
        }),
      ],
    );

    expect(items).toBe(0);
  });
});
