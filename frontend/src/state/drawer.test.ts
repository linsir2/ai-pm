/* 决策卡弹出状态机的判据（2026-10-29 主闭环对话流）。
   `waiting_on` 是唯一真相源；"用户收起"只能压住、不能改变该弹哪张。 */

import { describe, expect, it } from "vitest";

import type { RoundView } from "../api/types";
import { drawerChip, drawerFor, pendingDrawer } from "./drawer";

function round(over: Partial<RoundView> = {}): RoundView {
  return {
    round_id: "r-1",
    project_id: "p-1",
    user_input: "想做个导出",
    step: "seats_running",
    waiting_on: "none",
    seat_round_count: 1,
    seats: [],
    active_seats: [],
    nominated_seats: [],
    available_seats: [],
    claims: [],
    seat_packets: [],
    dropped: [],
    questions: [],
    list_items: [],
    advisories: [],
    ...over,
  } as RoundView;
}

describe("drawer 状态机", () => {
  it("waiting_on 决定弹哪张；没有等待就是 null", () => {
    expect(pendingDrawer(round({ waiting_on: "opening_card" }))).toBe("opening");
    expect(pendingDrawer(round({ waiting_on: "questionnaire", questions: [{}, {}] as never }))).toBe(
      "questionnaire",
    );
    expect(
      pendingDrawer(round({ waiting_on: "draft_card", draft: { proposals: [{}] } as never })),
    ).toBe("draft");
    // draft 为 undefined（老轮次/投影缺键）也不弹
    expect(pendingDrawer(round({ waiting_on: "draft_card", draft: undefined }))).toBeNull();
    expect(pendingDrawer(round({ waiting_on: "none" }))).toBeNull();
    expect(pendingDrawer(null)).toBeNull();
  });

  it("空问卷 / 空草案不弹（等着的那一步还没产出）", () => {
    expect(pendingDrawer(round({ waiting_on: "questionnaire" }))).toBeNull();
    expect(pendingDrawer(round({ waiting_on: "draft_card" }))).toBeNull();
  });

  it("收起只能压住，不能改变该弹哪张", () => {
    const r = round({ waiting_on: "opening_card" });
    expect(drawerFor(r, false)).toBe("opening");
    expect(drawerFor(r, true)).toBeNull();
  });

  it("chip 的文案带数量；没有待办就没有 chip", () => {
    expect(drawerChip("opening", round())).toEqual({ label: "复述卡", detail: "待你提交" });
    expect(drawerChip("questionnaire", round({ questions: [{}, {}] as never }))).toEqual({
      label: "问卷",
      detail: "2 题待答",
    });
    expect(
      drawerChip("draft", round({ draft: { proposals: [{}, {}, {}] } as never })),
    ).toEqual({ label: "草案", detail: "3 条候选" });
  });
});
