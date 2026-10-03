// @vitest-environment jsdom
/* 草案卡的动作：确认写入 / 全不勾 / **继续深挖**（C6，P14）。

   这一份钉两件事：按钮真的调到了 `onDig`；正在提交时它不可点（别让人连点两次）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DocumentView, ProposalView, RoundView } from "../api/types";
import { DraftDrawer } from "./DraftDrawer";
import { EvidenceScope } from "./EvidenceContext";

// vitest 没开 globals：testing-library 不会自动清理，连着渲染两个用例会互相看见
afterEach(cleanup);

const proposal: ProposalView = {
  proposal_id: "p01",
  entry_id: "r1#01",
  op: "add",
  target_label: "s5",
  text: "导出要有筛选",
  origin_type: "user",
  eids: ["E1"],
  rationale: null,
  kind: "text",
  warning: null,
};

function round(): RoundView {
  return {
    round_id: "r1",
    step: "awaiting_confirm",
    waiting_on: "draft",
    seat_round_count: 2,
    seats: ["seat.a"],
    nominated_seats: [],
    claims: [],
    seat_packets: [],
    questions: [],
    list_items: [],
    advisories: [],
    failure_reason: null,
    confirmed_version_no: null,
    form_id: null,
    project_id: "p1",
    user_input: "想法",
    draft: {
      draft_id: "d1",
      groups: [["你说过的", [proposal]]],
      proposals: [proposal],
      gaps: [],
    },
  } as unknown as RoundView;
}

function draw(over: Partial<Parameters<typeof DraftDrawer>[0]> = {}) {
  const props = {
    round: round(),
    doc: null as DocumentView | null,
    accepted: ["p01"],
    memoryAccepted: [],
    pending: false,
    onToggle: vi.fn(),
    onToggleMemory: vi.fn(),
    onSetAccepted: vi.fn(),
    onSubmit: vi.fn(),
    onDig: vi.fn(),
    onCollapse: vi.fn(),
    ...over,
  };
  // 出处胶囊要 context：真应用里由 App 的 `EvidenceScope` 提供
  render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <DraftDrawer {...props} />
    </EvidenceScope>,
  );
  return props;
}

describe("DraftDrawer", () => {
  it("M6：记忆那一档默认不勾，勾了才调 onToggleMemory", () => {
    const onToggleMemory = vi.fn();
    const base = round();
    const props = draw({
      round: {
        ...base,
        memory_proposals: [
          {
            proposal_id: "mem#1",
            op: "add",
            kind: "decision",
            text: "导出保留期是 30 天",
            refs: ["E1"],
            reason: "用户这一轮定下来的口径",
            target_id: null,
          },
        ],
      } as RoundView,
      onToggleMemory,
    });

    expect(screen.getByText(/这一轮攒下的记忆/)).toBeTruthy();
    expect(screen.getByText("导出保留期是 30 天")).toBeTruthy();
    // 默认不勾：按钮上是"不记"，而不是"记"
    expect(screen.getByText("不记")).toBeTruthy();
    fireEvent.click(screen.getByText("不记"));
    expect(onToggleMemory).toHaveBeenCalledWith("mem#1");
    expect(props.memoryAccepted).toEqual([]);
  });

  it("点『继续深挖』调到 onDig", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("button", { name: /继续深挖/ }));

    expect(props.onDig).toHaveBeenCalledTimes(1);
    expect(props.onSubmit).not.toHaveBeenCalled()
  });

  it("正在提交时按钮不可点", () => {
    draw({ pending: true });

    // 没装 jest-dom 的匹配器，直接看属性
    const button = screen.getByRole("button", { name: /继续深挖/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("『全不勾』把已勾的清空；『本批全接受』带上提议组", () => {
    const props = draw({
      round: roundWithAllThreeOrigins(),
      accepted: ["p01", "p02"],
    });

    fireEvent.click(screen.getByRole("button", { name: /全不勾/ }));
    expect(props.onSetAccepted).toHaveBeenLastCalledWith([]);

    fireEvent.click(screen.getByRole("button", { name: /本批全接受/ }));
    expect(props.onSetAccepted).toHaveBeenLastCalledWith(["p01", "p02", "p03"]);
  });

  it("默认勾选由上层按『你说过的 + 它替你判断的』给，提议组不在里面", () => {
    const proposals = [proposal, other("p02", "derived"), other("p03", "proposed")];

    // 界面只照 `accepted` 画勾：默认值来自 `defaultAccepted`（另一条纯函数用例钉着）
    draw({ round: roundWith(proposals), accepted: ["p01", "p02"] });

    const toggles = screen.getAllByRole("button", { name: /^(要|不要)$/ }) as HTMLButtonElement[];
    expect(toggles.map((button) => button.getAttribute("aria-pressed"))).toEqual([
      "true",
      "true",
      "false",
    ]);
  });
});

function other(proposalId: string, originType: ProposalView["origin_type"]): ProposalView {
  return { ...proposal, proposal_id: proposalId, origin_type: originType };
}

function roundWith(proposals: ProposalView[]): RoundView {
  return {
    ...round(),
    draft: {
      draft_id: "d1",
      groups: [
        ["你说过的", proposals.filter((item) => item.origin_type === "user")],
        ["它替你判断的", proposals.filter((item) => item.origin_type === "derived")],
        ["它提议的", proposals.filter((item) => item.origin_type === "proposed")],
      ],
      proposals,
      gaps: [],
    },
  } as unknown as RoundView;
}

function roundWithAllThreeOrigins(): RoundView {
  return roundWith([proposal, other("p02", "derived"), other("p03", "proposed")]);
}

// P29 眼睛检查 F3：真跑库里三条提案都落在 s12 → 后端吐出三句一模一样的告警。
// 旧写法拿告警文本当 React key，会报"same key"警告（行可能被丢掉），而且三句一样的话
// 用户分不清是哪几条。现在合并成一条 + 带条数。
it("相同的告警合并成一条并带条数（不再三句一样的话摆三遍）", () => {
  const base = roundWith([proposal]);
  const round = {
    ...base,
    advisories: [
      "s12：这条落在 s12，不在本轮去处里（席位替你判断的，确认前请看一眼）",
      "s12：这条落在 s12，不在本轮去处里（席位替你判断的，确认前请看一眼）",
      "s12：这条落在 s12，不在本轮去处里（席位替你判断的，确认前请看一眼）",
    ],
  } as RoundView;

  render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <DraftDrawer
        round={round}
        doc={null}
        accepted={[]}
        memoryAccepted={[]}
        pending={false}
        onToggle={() => undefined}
        onToggleMemory={() => undefined}
        onSetAccepted={() => undefined}
        onSubmit={() => undefined}
        onDig={() => undefined}
        onCollapse={() => undefined}
      />
    </EvidenceScope>,
  );

  const banners = screen.getAllByText(/不在本轮去处里/);
  expect(banners).toHaveLength(1);            // 一条，不是三条
  expect(banners[0]!.textContent).toContain("3 条");   // 条数说清楚
});
