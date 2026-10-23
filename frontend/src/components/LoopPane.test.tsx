// @vitest-environment jsdom
/* 闭环区里两份"按文本当 key"的列表（`P29` 眼睛检查 F1/F2）。

   真跑现场（asks 库）：39 条清单项里 **9 条文本重复**；留痕里同 `handle`+`reason` 也会撞。
   旧写法拿文本当 React key → 控制台一串 "same key" 警告，行可能被丢掉或复制。
   这一份钉两件事：**所有行都画出来**，而且**不再出现重复 key 警告**。 */

import { cleanup, render, within } from "@testing-library/react";
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
    asked_questions: [],
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
    web_search_network_access: false,
    write_scope: [],
    contradictions: [],
    proposed_offered: null,
    proposed_accepted: null,
    proposal_ignore_streak: 0,
    ...over,
  } as unknown as RoundView;
}

function draw(
  round_: RoundView,
  props: { elapsed?: string | null; pending?: string | null } = {},
) {
  return render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <LoopPane
        round={round_}
        pending={props.pending ?? null}
        elapsed={props.elapsed ?? null}
        onRetry={() => undefined}
        onAbandon={() => undefined}
        onSkipSeat={() => undefined}
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

  it("自动检索进来的材料不算「留痕」：不进那句「去处写错的」，也不挤占工具的条数", () => {
    const { container } = draw(
      round({
        seats: ["seat.a"],
        dropped: [
          {
            handle: "自动检索 1 份",
            reason: "这一轮还从材料库里按本轮输入检索并收敛了 1 份（不是你勾的，块已带进来）",
            source: "material_auto",
            seat_id: null,
            pass_index: 1,
          },
          { handle: "web_search", reason: "没搜到结果", source: "tool", seat_id: null, pass_index: 1 },
        ],
      }),
    );

    // 它读到了、只是没勾：落进折叠会被那句「去处写错的」说反话，而且每轮都会出现
    expect(container.textContent).not.toContain("自动检索 1 份");
    // 工具那一趟照旧单列（不受影响）
    expect(container.textContent).toContain("没搜到结果");
  });
});

describe("对话流折叠纪律（Trae 式：标题行常显，明细点展）", () => {
  function claimed(): RoundView {
    return round({
      claims: [
        {
          claim_id: "c1",
          eid: "E1",
          label: "need",
          target_label: "s5",
          text: "做导出",
          raw_span: "做导出",
          assumption_note: null,
        },
        {
          claim_id: "c2",
          eid: "E2",
          label: "fact",
          target_label: "s2",
          text: "有数据",
          raw_span: "有数据",
          assumption_note: null,
        },
      ],
    });
  }

  it("C1 主张默认收：标题行露计数，明细在没打开的 details 里（防全展回潮）", () => {
    const { container } = draw(claimed());

    const folded = container.querySelector("details.claims-fold:not([open])");
    expect(folded).toBeTruthy();
    expect(folded!.querySelector("summary")!.textContent).toContain("2 条");
    // 明细仍在 DOM（证据可点），只是默认不摊平
    expect(container.querySelectorAll(".claims .claim")).toHaveLength(2);
  });

  it("C10 时间线标题是叙述体（防回退名词体）", () => {
    const { container } = draw(
      claimed(),
    );

    expect(container.textContent).toContain("已切出 2 条主张");
  });

  it("C3 在跑席位由后端 pending stub 卡表示（'正在交回…'），交完翻'已完成'（防假圈/漏活态）", () => {
    const running = draw(
      round({
        step: "seats_running",
        waiting_on: "none",
        seats: ["seat.a", "seat.b"],
        active_seats: ["seat.a", "seat.b"],
        seat_round_count: 1,
        seat_packets: [
          {
            seat_id: "seat.a",
            title: "业务规则",
            status: "completed",
            pass_index: 1,
            packet_id: null,
            basis: "读了文档",
            opinions: [],
            judgment_count: 0,
            question_count: 0,
          },
          {
            seat_id: "seat.b",
            title: "前端交互",
            status: "pending",
            pass_index: 1,
            packet_id: null,
            basis: "",
            opinions: [],
            judgment_count: 0,
            question_count: 0,
          },
        ],
      }),
      { pending: "seat" },
    );

    expect(running.container.textContent).toContain("正在交回");
    expect(running.container.textContent).toContain("业务规则");
    expect(running.container.textContent).toContain("前端交互");
  });

  it("C4 留痕默认收：标题行露条数，明细仍在 DOM（防 16 条全展）", () => {
    const { container } = draw(
      round({
        seats: ["seat.a"],
        dropped: [drop("seat.a:O1", "没交代去向"), drop("seat.a:O2", "去处写错")],
      }),
    );

    const folded = container.querySelector("details.drops-fold:not([open])");
    expect(folded).toBeTruthy();
    expect(folded!.querySelector("summary")!.textContent).toContain("2 条");
    expect(container.querySelectorAll("ul.drops li.drop")).toHaveLength(2);
  });

  it("C5-note 清单重复时留一行提示，行数不动（不断言改不动）", () => {
    const { container } = draw(
      round({ list_items: ["同一件事", "同一件事", "另一件"] }),
    );

    expect(container.querySelectorAll(".loop__group .entry")).toHaveLength(3);
    expect(container.textContent).toContain("重复");
  });

  it("C7 答过的批进时间线、答案直接可见（防答完无痕/藏进折叠行）", () => {
    const { container } = draw(
      round({
        answer_sets: [
          { form_id: "f1", answered: 1, skipped: 1, answers: ["用不用云 → 用云；备注：预算已批"] },
          { form_id: "f2", answered: 2, skipped: 0, answers: ["范围 → 全量", "时机 → 月底"] },
        ],
      }),
    );

    // 节点「你 · 答过 2 批」：批次头复用 answerBatchSummary，答案串就是「题目 → 选项」整串
    expect(container.querySelector(".answers")).toBeTruthy();
    expect(container.textContent).toContain("答过 2 批");
    expect(container.textContent).toContain("第 1 批 · 已答 1 · 跳过 1");
    expect(container.textContent).toContain("第 2 批 · 已答 2");
    expect(container.textContent).toContain("用不用云 → 用云");
    expect(container.textContent).toContain("月底");
    // 旧的底部折叠行退休
    expect(container.querySelector("details.history-fold")).toBeNull();
  });

  it("C11 整合按钮只在该动作可做时出现（等答/等确认），做完不留死按钮", () => {
    const draft = {
      draft_id: "d1",
      groups: [{}, {}],
      proposals: [{ proposal_id: "p1", op: "add" }],
    } as unknown as RoundView["draft"];
    const questionsQ = [{ question_id: "q1" }] as unknown as RoundView["questions"];

    const asking = draw(round({ step: "awaiting_answers", waiting_on: "questionnaire", questions: questionsQ }));
    expect(within(asking.container).getByRole("button", { name: /查看问卷/ })).toBeTruthy();

    const confirming = draw(round({ step: "awaiting_confirm", waiting_on: "draft_card", draft }));
    expect(within(confirming.container).getByRole("button", { name: /查看草案/ })).toBeTruthy();

    const done = draw(round({ step: "done", waiting_on: "none", draft, questions: questionsQ }));
    expect(within(done.container).queryByRole("button", { name: /查看草案/ })).toBeNull();
    expect(within(done.container).queryByRole("button", { name: /查看问卷/ })).toBeNull();
  });

  it("C8b 跑动中才显示开局时长，停了就不显示（防'已跑 28805 分钟'这种谎言）", () => {
    const shown = draw(round({}), { elapsed: "开局 3 分钟", pending: "seat" });
    expect(shown.container.textContent).toContain("开局 3 分钟");

    const idle = draw(round({}), { elapsed: "开局 3 分钟" });
    expect(idle.container.textContent).not.toContain("开局");

    const hidden = draw(round({}));
    expect(hidden.container.textContent).not.toContain("开局");
  });
});
