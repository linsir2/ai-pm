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
    waiting_on: "draft_card",
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
    selectionMatches: true,
    onToggle: vi.fn(),
    onToggleMemory: vi.fn(),
    onSetAccepted: vi.fn(),
    onSubmit: vi.fn(),
    onDig: vi.fn(),
    onSimInquiry: vi.fn(),
    onSimReview: vi.fn(),
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
  it("replace给出真实old→new与旧refs，跳转携带精确Entry含#", () => {
    const item = { ...proposal, op: "replace", before: {
      entry_id: "r1#01", text: "基线旧文", refs: ["E0"],
    }, text: "修订后正文", base_version_no: 2 };
    const base = round();
    const props = draw({ round: { ...base, draft: {
      ...base.draft!, proposals: [item], groups: [["你说过的", [item]]],
    } }, onOpenTarget: vi.fn() });
    expect(screen.getByText("基线旧文")).toBeTruthy();
    expect(screen.getByText("修订后正文")).toBeTruthy();
    expect(screen.getByText(/E0/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "s5" }));
    expect(props.onOpenTarget).toHaveBeenCalledWith("s5", "r1#01");
  });

  it("remove显示被撤基线而非payload/current；未知旧值明确说未知", () => {
    const base = round();
    const items = [{ ...proposal, op: "remove", text: "不是基线", before: {
      entry_id: "r1#01", text: "实际旧文", refs: ["E0"],
    } }, { ...proposal, proposal_id: "p02", op: "replace", before: null }];
    draw({ round: { ...base, draft: { ...base.draft!, proposals: items,
      groups: [["你说过的", items]],
    } } });
    expect(screen.getByText("实际旧文")).toBeTruthy();
    expect(screen.queryByText("不是基线")).toBeNull();
    expect(screen.getByText(/基线旧值未知/)).toBeTruthy();
  });

  it("身份未匹配的短窗口与done历史卡都禁提交/记忆选择", () => {
    const props = draw({ selectionMatches: false });
    fireEvent.click(screen.getByRole("button", { name: /确认写入/ }));
    expect(props.onSubmit).not.toHaveBeenCalled();
    cleanup();
    const done = draw({ round: { ...round(), step: "done", waiting_on: "none" } });
    expect(screen.getByRole("button", { name: /确认写入/ }).hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: /确认写入/ }));
    expect(done.onSubmit).not.toHaveBeenCalled();
  });

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

  it("点『去模拟调研』调到 onSimInquiry（M10：草案卡唯一新入口）", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("button", { name: /去模拟调研/ }));

    expect(props.onSimInquiry).toHaveBeenCalledTimes(1);
    expect(props.onSubmit).not.toHaveBeenCalled()
  });

  it("点『模拟评测』调到 onSimReview（M11：减方向的入口）", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("button", { name: /模拟评测/ }));

    expect(props.onSimReview).toHaveBeenCalledTimes(1);
    expect(props.onSubmit).not.toHaveBeenCalled()
  });

  it("引用了模拟推演的提案带『模拟评测依据』tag", () => {
    const round_ = round();
    round_.draft!.proposals[0]!.simulation_based = true;
    draw({ round: round_ });

    expect(screen.getByText("模拟评测依据")).toBeTruthy();
  });

  // 2026-10-29「整块重述式 add」防线：overlap 有值的提案，后端把机械判定合成人话进
  // warning——⚠ 透传渲染已具备，这条钉住「与已有内容重叠」字样必须看得见。
  it("overlap 有值 + warning 合成句：卡上出现『与已有内容重叠』字样", () => {
    const overlapProposal: ProposalView = {
      ...proposal,
      proposal_id: "p99",
      overlap: "3/4",
      warning: "这条与已有内容重叠（4 行里 3 行逐字相同），已默认不勾，看清再勾",
    };
    draw({ round: roundWith([overlapProposal]) });

    expect(screen.getByText(/与已有内容重叠/)).toBeTruthy();
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
        onSimInquiry={() => undefined}
        onSimReview={() => undefined}
        onCollapse={() => undefined}
      />
    </EvidenceScope>,
  );

  const banners = screen.getAllByText(/不在本轮去处里/);
  expect(banners).toHaveLength(1);            // 一条，不是三条
  expect(banners[0]!.textContent).toContain("3 条");   // 条数说清楚
});

it("多条**不同**的告警折成一行计数，明细在 DOM（防黄条刷屏）", () => {
  const base = roundWith([proposal]);
  const round = {
    ...base,
    advisories: [
      "s3：这条落在 s3，不在本轮去处里（席位替你判断的，确认前请看一眼）",
      "s10：这条落在 s10，不在本轮去处里（席位替你判断的，确认前请看一眼）",
    ],
  } as RoundView;

  const { container } = render(
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
        onSimInquiry={() => undefined}
        onSimReview={() => undefined}
        onCollapse={() => undefined}
      />
    </EvidenceScope>,
  );

  const folded = container.querySelector("details.advisory-fold:not([open])");
  expect(folded).toBeTruthy();
  expect(folded!.querySelector("summary")!.textContent).toContain("2");
  // 明细仍在 DOM（点开就能看）
  expect(container.textContent).toContain("s3");
  expect(container.textContent).toContain("s10");
});

describe("草案提案的去处可点（跳文档）", () => {
  it("给了 onOpenTarget：点去处调到它，载荷是那条 target_label（防死文本）", () => {
    const props = draw({ onOpenTarget: vi.fn() });

    fireEvent.click(screen.getByRole("button", { name: "s5" }));

    expect(props.onOpenTarget).toHaveBeenCalledWith("s5");
  });

  it("没给就不出按钮（老消费者与老测试不受影响）", () => {
    draw();

    expect(screen.queryByRole("button", { name: "s5" })).toBeNull();
  });
});
