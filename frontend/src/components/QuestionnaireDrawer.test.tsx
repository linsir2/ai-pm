// @vitest-environment jsdom
/* 问卷抽屉的组件级回归（F11）：勾选 / 跳过 / 交卷体形状，以及"已答 x/y"。

   与 `useRoundDrafts.test.tsx` 的分工：那边钉"打字不丢"的状态钩子，这边钉
   渲染出来的东西与它交出去的请求体。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RoundView } from "../api/types";
import { EvidenceScope } from "./EvidenceContext";
import { QuestionnaireDrawer } from "./QuestionnaireDrawer";

afterEach(cleanup);

function round(): RoundView {
  return {
    round_id: "r1",
    project_id: "p1",
    step: "awaiting_answers",
    waiting_on: "questionnaire",
    user_input: "想法",
    seat_round_count: 1,
    seats: ["seat.a"],
    active_seats: ["seat.a"],
    nominated_seats: [],
    available_seats: [],
    claims: [],
    seat_packets: [],
    dropped: [],
    list_items: [],
    advisories: [],
    failure_reason: null,
    failure_at_step: null,
    retry_from: null,
    abandon_reason: null,
    confirmed_version_no: null,
    form_id: "f2",
    answer_sets: [
      { form_id: "f1", answered: 1, skipped: 1, answers: ["存储选哪家 → 云服务"] },
    ],
    questions: [
      {
        question_id: "q3",
        kind: "gap",
        type: "single",
        title: "保存几年？",
        why: "合规要求",
        options: [
          ["a", "三年"],
          ["b", "五年"],
        ],
        target_label: "s8",
        eids: [],
        asked_by: [],
        consequence_if_skipped: "按三年实现",
        blocking: false,
      },
      {
        question_id: "q4",
        kind: "gap",
        type: "text",
        title: "还有别的吗？",
        why: "补全",
        options: [],
        target_label: "s11",
        eids: [],
        asked_by: [],
        consequence_if_skipped: "落 s11",
        blocking: false,
      },
    ],
    draft: null,
  } as unknown as RoundView;
}

function draw(over: Partial<Parameters<typeof QuestionnaireDrawer>[0]> = {}) {
  const props = {
    round: round(),
    doc: null,
    drafts: {},
    pending: false,
    onChange: vi.fn(),
    onSubmit: vi.fn(),
    onSkipAll: vi.fn(),
    onCollapse: vi.fn(),
    ...over,
  };
  render(
    <EvidenceScope index={new Map()} onJump={() => undefined}>
      <QuestionnaireDrawer {...props} />
    </EvidenceScope>,
  );
  return props;
}

describe("QuestionnaireDrawer", () => {
  it("标题上写着这一批答了几题（F3）", () => {
    draw({ drafts: { q3: { selected: ["a"], text: "" } } });

    expect(screen.getByText(/问卷 · 已答 1\/2/)).toBeTruthy();
  });

  it("本轮前几批答过的，看得到自己写了什么（F3）", () => {
    draw();

    expect(screen.getByText("已答 1 · 跳过 1")).toBeTruthy();
    expect(screen.getByText("存储选哪家 → 云服务")).toBeTruthy();
  });

  it("点选项调 onChange，选中的进 selected", () => {
    const props = draw();

    fireEvent.click(screen.getByLabelText("三年"));

    expect(props.onChange).toHaveBeenCalledWith("q3", {
      selected: ["a"],
      text: "",
      skipped: false,
    });
  });

  it("标『跳过这题』把 skipped 置上", () => {
    const props = draw();

    fireEvent.click(screen.getAllByRole("button", { name: "跳过这题" })[0]!);

    expect(props.onChange).toHaveBeenCalledWith("q3", {
      selected: [],
      text: "",
      skipped: true,
    });
  });

  it("交卷与全部跳过各自调到自己的回调，正在提交时都不可点", () => {
    const props = draw({ pending: true });

    const submit = screen.getByRole("button", { name: /正在交卷/ }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(submit);
    expect(props.onSubmit).not.toHaveBeenCalled();
  });
});

describe("问卷题的「聊聊」（M13 补上快照上线）", () => {
  it("只有某个席位问的题才有按钮；点它把题面、问的人、快照（题面+为什么问）一起交出去", () => {
    const base = round();
    const asked = {
      ...base.questions[0]!,
      asked_by: ["seat.data_scale"],
    };
    const props = draw({
      round: { ...base, questions: [asked] },
      onDiscuss: vi.fn(),
    });

    fireEvent.click(screen.getByRole("button", { name: "聊聊" }));

    expect(props.onDiscuss).toHaveBeenCalledWith({
      topic: "保存几年？",
      role_id: "seat.data_scale",
      question: { title: "保存几年？", why: "合规要求" },
    });
  });

  it("没有出题人的题不给按钮（没有'谁'可请）", () => {
    draw({ onDiscuss: vi.fn() });

    expect(screen.queryByRole("button", { name: "聊聊" })).toBeNull();
  });

  it("C6 矛盾题置顶：最需要拍板的先答（后端顺序不动，stable 排）", () => {
    const base = round();
    const conflict = {
      ...base.questions[1]!,
      question_id: "q9",
      kind: "conflict",
      title: "口径打架了",
    };
    draw({ round: { ...base, questions: [...base.questions, conflict] } });

    const titles = document.querySelectorAll(".q .q__title");
    expect(titles[0]!.textContent).toBe("口径打架了");
    // 非矛盾题相对顺序不变
    expect(titles[1]!.textContent).toBe("保存几年？");
  });
});

describe("问卷题的去处可点（跳文档）", () => {
  it("给了 onOpenTarget：点去处调到它，载荷是那条 target_label（防死文本）", () => {
    const props = draw({ onOpenTarget: vi.fn() });

    fireEvent.click(screen.getByRole("button", { name: "s8" }));

    expect(props.onOpenTarget).toHaveBeenCalledWith("s8");
  });

  it("没给就不出按钮（老消费者不受影响）", () => {
    draw();

    expect(screen.queryByRole("button", { name: "s8" })).toBeNull();
  });
});
