// @vitest-environment jsdom
/* 这一份是**回归测试**：用户报过"问卷打了一半的字被清空""收起抽屉又自己弹起来"。
   根因是当时按 [roundId, round] 重置作答现场，而事件推送/重读工作区都会换掉
   round 对象的引用。这里把两条都钉住。 */

import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AwaitingMode } from "../api/payload";
import type { RoundView } from "../api/types";
import { useRoundDrafts } from "./useRoundDrafts";

function round(over: Partial<RoundView> = {}): RoundView {
  return {
    round_id: "r1",
    step: "awaiting_answers",
    waiting_on: "questionnaire",
    seat_round_count: 1,
    seats: ["seat.a"],
    nominated_seats: [{ seat_id: "seat.a", title: "A", focus: "为什么请它" }],
    claims: [{ claim_id: "c1", label: "need", target_label: "s5" }],
    seat_packets: [],
    questions: [{ question_id: "q1", title: "问点什么" }],
    list_items: [],
    draft: null,
    advisories: [],
    failure_reason: null,
    confirmed_version_no: null,
    form_id: "f1",
    project_id: "p1",
    user_input: "想法",
    ...over,
  } as unknown as RoundView;
}

function render(initialRound: RoundView, initialMode: AwaitingMode) {
  return renderHook(
    ({ value, mode }: { value: RoundView; mode: AwaitingMode }) =>
      useRoundDrafts(value, mode),
    { initialProps: { value: initialRound, mode: initialMode } },
  );
}

describe("useRoundDrafts", () => {
  it("同一轮里数据刷新（新的 round 对象）不清空已经答的内容", () => {
    const { result, rerender } = render(round(), "questionnaire");

    act(() => result.current.changeAnswer("q1", { selected: ["a"], text: "我打的字" }));
    expect(result.current.answers.q1!.text).toBe("我打的字");

    // 事件推送 / 重读工作区：同一轮、新对象
    rerender({ value: round({ step: "seats_running" }), mode: "loading" });
    expect(result.current.answers.q1!.text).toBe("我打的字");
  });

  it("用户收起抽屉后，普通刷新不会把它再弹开", () => {
    const { result, rerender } = render(round(), "questionnaire");
    act(() => result.current.setCollapsed(true));
    expect(result.current.collapsed).toBe(true);

    rerender({ value: round({ seat_round_count: 2 }), mode: "loading" });
    expect(result.current.collapsed).toBe(true);
  });

  it("该勾草案了（模式真的换了）就重新展开", () => {
    const withDraft = round({
      waiting_on: "draft_card",
      draft: { draft_id: "d1", groups: [], proposals: [], gaps: [] },
    });
    const { result, rerender } = render(round(), "questionnaire");
    act(() => result.current.setCollapsed(true));

    rerender({ value: withDraft, mode: "draft" });
    expect(result.current.collapsed).toBe(false);
    expect(result.current.accepted).toEqual([]);
  });

  it("换成另一轮才清空（新一轮不带上一轮的勾选）", () => {
    const { result, rerender } = render(round(), "questionnaire");
    act(() => result.current.changeAnswer("q1", { selected: ["a"], text: "上一轮的字" }));

    rerender({ value: round({ round_id: "r2" }), mode: "questionnaire" });
    expect(result.current.answers).toEqual({});
  });

  it("新一轮的复述卡自动弹出：上一轮收起过的状态不许带过来（2026-10-29 回归）", () => {
    const opening = round({ waiting_on: "opening_card", step: "claims_ready", questions: [] });
    const { result, rerender } = render(opening, "opening");
    act(() => result.current.setCollapsed(true));
    expect(result.current.collapsed).toBe(true);

    rerender({ value: round({ round_id: "r2", waiting_on: "opening_card", step: "claims_ready" }), mode: "opening" });
    expect(result.current.collapsed).toBe(false);
  });

  it("waiting_on 翻到 opening_card（mode 变 opening）时浮层自动弹出，即便此前收起", () => {
    const opening = round({ waiting_on: "opening_card", step: "claims_ready", questions: [] });
    const { result, rerender } = render(round({ waiting_on: "none", step: "started" }), "loading");
    act(() => result.current.setCollapsed(true));

    rerender({ value: opening, mode: "opening" });
    expect(result.current.collapsed).toBe(false);
  });
});

describe("useRoundDrafts · 联网授权（P3a）", () => {
  it("allow_web 默认关，逐条批准默认一条都不勾", () => {
    const { result } = render(round(), "opening");

    expect(result.current.allowWeb).toBe(false);
    expect(result.current.approvedRequestIds).toEqual([]);
  });

  it("打开 allow_web、勾一条申请：同一轮刷新不清空", () => {
    const { result, rerender } = render(round(), "opening");

    act(() => result.current.setAllowWeb(true));
    act(() => result.current.toggleWebRequest("w1"));
    expect(result.current.allowWeb).toBe(true);
    expect(result.current.approvedRequestIds).toEqual(["w1"]);

    rerender({ value: round({ step: "claims_ready" }), mode: "opening" });
    expect(result.current.allowWeb).toBe(true);
    expect(result.current.approvedRequestIds).toEqual(["w1"]);
  });

  it("换一轮重置 allow_web 与逐条批准（上一轮的授权绝不带进下一轮）", () => {
    const { result, rerender } = render(round(), "opening");
    act(() => result.current.setAllowWeb(true));
    act(() => result.current.toggleWebRequest("w1"));

    rerender({ value: round({ round_id: "r2" }), mode: "opening" });
    expect(result.current.allowWeb).toBe(false);
    expect(result.current.approvedRequestIds).toEqual([]);
  });
});
