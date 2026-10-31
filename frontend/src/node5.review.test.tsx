// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { defaultAccepted } from "./api/payload";
import type { DraftView, ProposalView, RoundView } from "./api/types";
import { DraftDrawer } from "./components/DraftDrawer";
import { EvidenceScope } from "./components/EvidenceContext";

afterEach(cleanup);

const warning =
  "这轮输入没有提到「一句话描述」，但这条改/删了它（en-bootstrap）。确认前看一眼。";

const proposal: ProposalView = {
  proposal_id: "p01",
  entry_id: "en-bootstrap",
  op: "replace",
  target_label: "s5/order-export.one_liner",
  text: "把订单导出成表格",
  origin_type: "user",
  eids: ["E1"],
  rationale: null,
  kind: "text",
  warning,
  requires_review: true,
};

const draft: DraftView = {
  draft_id: "d1",
  groups: [["你说过的", [proposal]]],
  proposals: [proposal],
  gaps: [],
};

const round = {
  round_id: "r1",
  step: "awaiting_confirm",
  waiting_on: "draft_card",
  seat_round_count: 1,
  seats: [],
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
  user_input: "把主流程的第一步改成最近30天",
  draft,
} as unknown as RoundView;

it("requires_review=true 不进 defaultAccepted，但 warning 仍走既有 ⚠ 通道显示", () => {
  expect(defaultAccepted(draft)).toEqual([]);

  render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <DraftDrawer
        round={round}
        doc={null}
        accepted={[]}
        memoryAccepted={[]}
        pending={false}
        selectionMatches
        onToggle={vi.fn()}
        onToggleMemory={vi.fn()}
        onSetAccepted={vi.fn()}
        onSubmit={vi.fn()}
        onDig={vi.fn()}
        onSimInquiry={vi.fn()}
        onSimReview={vi.fn()}
        onCollapse={vi.fn()}
      />
    </EvidenceScope>,
  );

  expect(screen.getByText(/这轮输入没有提到/)).toBeTruthy();
});

it("新增到已有内容且需过目的 add 也不进默认勾选", () => {
  const addToExisting: ProposalView = {
    ...proposal,
    proposal_id: "p04",
    op: "add",
    warning:
      "这轮输入没有提到「异常与边界」，但这条新增到了已有内容里（new-1）。确认前看一眼。",
    requires_review: true,
  };
  const marked: DraftView = {
    ...draft,
    groups: [["你说过的", [addToExisting]]],
    proposals: [addToExisting],
  };
  expect(defaultAccepted(marked)).toEqual([]);
});

it("越界与批内重复两种 requires_review 都不进默认勾选", () => {
  const scope = {
    ...proposal,
    proposal_id: "p02",
    warning: "这条落在 s6，不在本轮去处里（席位替你判断的，确认前请看一眼）",
    requires_review: true,
  };
  const duplicate = {
    ...proposal,
    proposal_id: "p03",
    warning: "本批有 2 条几乎相同的新增（p02、p03），全选会冲突；保留一条即可。",
    requires_review: true,
  };
  const marked: DraftView = {
    ...draft,
    groups: [["你说过的", [scope, duplicate]]],
    proposals: [scope, duplicate],
  };

  expect(defaultAccepted(marked)).toEqual([]);
});
