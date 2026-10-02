// @vitest-environment jsdom
/* 闭环区里两份"按文本当 key"的列表（`P29` 眼睛检查 F1/F2）。

   真跑现场（asks 库）：39 条清单项里 **9 条文本重复**；留痕里同 `handle`+`reason` 也会撞。
   旧写法拿文本当 React key → 控制台一串 "same key" 警告，行可能被丢掉或复制。
   这一份钉两件事：**所有行都画出来**，而且**不再出现重复 key 警告**。 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DroppedView, RoundView } from "../api/types";
import { LoopPane } from "./LoopPane";
import { EvidenceScope } from "./EvidenceContext";

afterEach(cleanup);

function drop(handle: string, reason: string): DroppedView {
  return { handle, reason, source: "unaccounted", seat_id: null, pass_index: 1 };
}

function round(over: Partial<RoundView>): RoundView {
  return {
    round_id: "r1",
    project_id: "p1",
    user_input: "想法",
    step: "awaiting_confirm",
    waiting_on: "draft_card",
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
    draft: null,
    advisories: [],
    failure_reason: null,
    failure_at_step: null,
    retry_from: null,
    abandon_reason: null,
    confirmed_version_no: null,
    form_id: null,
    answer_sets: [],
    materials: [],
    web_queries: [],
    web_queries_chosen: [],
    web_search_available: false,
    write_scope: [],
    contradictions: [],
    proposed_offered: null,
    proposed_accepted: null,
    proposal_ignore_streak: 0,
    ...over,
  } as unknown as RoundView;
}

function draw(round_: RoundView) {
  return render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <LoopPane
        round={round_}
        pending={null}
        onRetry={() => undefined}
        onAbandon={() => undefined}
        onOpenQuestionnaire={() => undefined}
        onOpenDraft={() => undefined}
      />
    </EvidenceScope>,
  );
}

describe("闭环区的列表（P29 F1/F2）", () => {
  it("清单项文本重复时**两条都画出来**，且不再报重复 key", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { container } = draw(
      round({
        list_items: [
          "备件同步失败降级策略（备用通道/人工导入）待明确",
          "备件同步失败降级策略（备用通道/人工导入）待明确",
          "点检记录导出是否包含照片附件待确认",
        ],
      }),
    );

    expect(container.querySelectorAll(".loop__group .entry")).toHaveLength(3);
    expect(spy.mock.calls.some(([message]) =>
      String(message).includes("same key"))).toBe(false);
    spy.mockRestore();
  });

  it("没挂到席位包上的留痕：同 handle+reason 也两条都在", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { container } = draw(
      round({
        seats: ["seat.a"],          // 席位轮那一段（留痕挂在它下面）要有席位才渲染
        dropped: [drop("seat.a:O1", "没交代去向"), drop("seat.a:O1", "没交代去向")],
      }),
    );

    expect(container.querySelectorAll("ul.drops li.drop")).toHaveLength(2);
    expect(spy.mock.calls.some(([message]) =>
      String(message).includes("same key"))).toBe(false);
    spy.mockRestore();
  });
});
