// @vitest-environment jsdom
/* 讨论托盘（M9-T6）：开聊卡（角色三选、问卷锚点预填）＋线程 inline 展开
   （追问 / 总结成卡 / 收卡 / 关掉）。门控全在 `state/discussions.ts` 的纯函数里，
   这里只断言"门控之后界面上画了什么"。 */

import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CardView, ThreadView } from "../api/types";
import type { DiscussionRoleOption } from "../state/discussions";
import { DiscussionTray } from "./DiscussionTray";

afterEach(cleanup);

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

function turn(index: number, role: "user" | "assistant", author?: string, round = 1) {
  return {
    index,
    role,
    text: `第${index}轮`,
    based_on: [],
    author: author ?? role,
    round,
  };
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

const ROLES: DiscussionRoleOption[] = [
  { role_id: "dlg.challenger", title: "反方", focus: "找漏洞、要反例、泼冷水" },
  { role_id: "dlg.user_voice", title: "用户之声", focus: "替真实用户说话" },
  { role_id: "dlg.analogist", title: "类比者", focus: "别人家怎么做过" },
];

function draw(over: Partial<Parameters<typeof DiscussionTray>[0]> = {}) {
  const props = {
    threads: [] as ThreadView[],
    roles: ROLES,
    turnLimit: 30,
    anchor: null,
    pending: false,
    onStartPanel: vi.fn().mockResolvedValue(null),
    onRunRound: vi.fn().mockResolvedValue(null),
    onJoin: vi.fn(),
    onAbortRun: vi.fn(),
    onSay: vi.fn(),
    onCards: vi.fn(),
    onKeep: vi.fn(),
    onDismiss: vi.fn(),
    onVerify: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
  render(<DiscussionTray {...props} />);
  return props;
}

describe("DiscussionTray", () => {
  it("空态说人话，并且开跑 panel 上三个固定角色都在（多选）", () => {
    draw();

    expect(screen.getByText(/还没开过讨论/)).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /反方/ })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /用户之声/ })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: /类比者/ })).toBeTruthy();
  });

  it("默认勾选三个固定角色；点开跑把主题、名单、轮数交出去", async () => {
    const props = draw();

    expect(
      (screen.getByRole("checkbox", { name: /反方/ }) as HTMLInputElement).checked,
    ).toBe(true);
    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "导出要不要做筛选" } });
    fireEvent.click(screen.getByRole("button", { name: "开跑" }));

    expect(props.onStartPanel).toHaveBeenCalledWith(
      {
        topic: "导出要不要做筛选",
        role_ids: ["dlg.challenger", "dlg.user_voice", "dlg.analogist"],
        question: null,
      },
      false,
    );
  });

  it("名单可加减：取消一个再开跑，名单里就没有它", async () => {
    const props = draw();

    fireEvent.click(screen.getByRole("checkbox", { name: /类比者/ }));
    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "导出要不要做筛选" } });
    fireEvent.click(screen.getByRole("button", { name: "开跑" }));

    expect(props.onStartPanel).toHaveBeenCalledWith(
      {
        topic: "导出要不要做筛选",
        role_ids: ["dlg.challenger", "dlg.user_voice"],
        question: null,
      },
      false,
    );
  });

  it("全取消就开不了跑（后端口径 1–4，界面先拦）", () => {
    const props = draw();

    for (const name of [/反方/, /用户之声/, /类比者/]) {
      fireEvent.click(screen.getByRole("checkbox", { name }));
    }
    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "导出要不要做筛选" } });

    expect(
      (screen.getByRole("button", { name: "开跑" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(props.onStartPanel).not.toHaveBeenCalled();
  });

  it("勾选「先跑一轮调研」：开跑时把 runInquiry=true 交出去（M13）", () => {
    const props = draw();

    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "导出要不要做筛选" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /先跑一轮调研/ }));
    fireEvent.click(screen.getByRole("button", { name: "开跑" }));

    expect(props.onStartPanel).toHaveBeenCalledWith(
      {
        topic: "导出要不要做筛选",
        role_ids: ["dlg.challenger", "dlg.user_voice", "dlg.analogist"],
        question: null,
      },
      true,
    );
  });

  it("带底稿的线程：头上写「开题调研底稿 N 条」（M13）", () => {
    draw({ threads: [thread({ findings_count: 3 })] });

    expect(screen.getByText(/开题调研底稿 3 条/)).toBeTruthy();
  });

  it("别的动作在途：开跑按钮禁用，按钮旁写明正在做什么（S1）", () => {
    const props = draw({ busy: true, busyText: "正在调研…" });

    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "导出要不要做筛选" } });

    expect(screen.getByRole("button", { name: "开跑" })).toHaveProperty("disabled", true);
    expect(screen.getByText("正在调研…")).toBeTruthy();
    expect(props.onStartPanel).not.toHaveBeenCalled();
  });

  it("空闲且主题/名单齐：开跑可点（S1）", () => {
    draw({ busy: false });

    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "导出要不要做筛选" } });

    expect(screen.getByRole("button", { name: "开跑" })).toHaveProperty("disabled", false);
  });

  it("主题留空时不发请求（后端 topic 必填，界面不点亮一个必然 422 的按钮）", () => {
    const props = draw();

    const start = screen.getByRole("button", { name: "开跑" }) as HTMLButtonElement;
    expect(start.disabled).toBe(true);
    expect(props.onStartPanel).not.toHaveBeenCalled();
  });

  it("问卷带进来的锚点：主题框有值、问的那个人默认勾上（席位 id 也照收）", () => {
    const props = draw({ anchor: { topic: "保存几年？", role_id: "seat.data_scale" } });

    expect((screen.getByLabelText("讨论主题") as HTMLTextAreaElement).value).toBe("保存几年？");
    expect(
      (screen.getByRole("checkbox", { name: /seat.data_scale/ }) as HTMLInputElement).checked,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "开跑" }));
    expect(props.onStartPanel).toHaveBeenCalledWith(
      {
        topic: "保存几年？",
        role_ids: ["seat.data_scale", "dlg.challenger", "dlg.user_voice", "dlg.analogist"],
        question: null,
      },
      false,
    );
  });

  it("线程 inline 展开：一对轮次按「你／反方」标出来", () => {
    draw({
      threads: [
        thread({
          turns: [
            { index: 0, role: "user", text: "先只做全量", based_on: [], author: "user", round: 1 },
            { index: 1, role: "assistant", text: "那筛选留到有人骂再加", based_on: ["E1"], author: "assistant", round: 1 },
          ],
        }),
      ],
    });

    expect(screen.getByText("导出要不要做筛选")).toBeTruthy();
    // 只在这一段对话里找"你／反方"：开聊卡上那三个角色也写着同样的词
    const log = within(screen.getByRole("log", { name: "导出要不要做筛选" }));
    expect(log.getByText("先只做全量")).toBeTruthy();
    expect(log.getByText("那筛选留到有人骂再加")).toBeTruthy();
    expect(log.getByText("你")).toBeTruthy();
    expect(log.getByText("反方")).toBeTruthy();
  });

  it("追问：把线程 id 与这句话交出去（空话点不动）", () => {
    const props = draw({ threads: [thread()] });

    expect((screen.getByRole("button", { name: "说这一轮" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    fireEvent.change(screen.getByLabelText("说这一轮"), { target: { value: "先只做全量" } });
    fireEvent.click(screen.getByRole("button", { name: "说这一轮" }));

    expect(props.onSay).toHaveBeenCalledWith("d-1", "先只做全量");
  });

  it("总结成卡与关掉线程各把 id 交出去", () => {
    const props = draw({ threads: [thread({ turns: [turn(0, "user"), turn(1, "assistant")] })] });

    fireEvent.click(screen.getByRole("button", { name: "总结成卡" }));
    expect(props.onCards).toHaveBeenCalledWith("d-1");

    fireEvent.click(screen.getByRole("button", { name: "关掉这条" }));
    expect(props.onClose).toHaveBeenCalledWith("d-1");
  });

  it("新卡三条路都通：收种子 / 收材料 / 忽略各把 `as` 交出去", () => {
    const props = draw({ threads: [thread({ cards: [card()] })] });
    const first = screen.getByRole("article");

    fireEvent.click(within(first).getByRole("button", { name: "收下为下一轮输入" }));
    expect(props.onKeep).toHaveBeenLastCalledWith("d-1", "C1", "seed");

    fireEvent.click(within(first).getByRole("button", { name: "收下为材料" }));
    expect(props.onKeep).toHaveBeenLastCalledWith("d-1", "C1", "material");

    fireEvent.click(within(first).getByRole("button", { name: "忽略" }));
    expect(props.onDismiss).toHaveBeenCalledWith("d-1", "C1");
  });

  it("新卡还能「拿去验证」（M13）：把原话交给上层起聚焦调研", () => {
    const props = draw({ threads: [thread({ cards: [card()] })] });

    fireEvent.click(screen.getByRole("button", { name: "拿去验证" }));

    expect(props.onVerify).toHaveBeenCalledWith("导出先只做全量，筛选等有人骂再加");
  });

  it("已处理的卡不再给按钮，只显示各状态的文案", () => {
    draw({
      threads: [
        thread({
          cards: [
            card({ card_id: "C1", state: "kept", kept_as: "seed" }),
            card({ card_id: "C2", state: "kept", kept_as: "material", kept_ref: "m-9" }),
            card({ card_id: "C3", state: "dismissed" }),
          ],
        }),
      ],
    });

    expect(screen.queryByRole("button", { name: "收下为下一轮输入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "忽略" })).toBeNull();
    expect(screen.getByText("已收下为下一轮输入")).toBeTruthy();
    expect(screen.getByText("已收下为材料")).toBeTruthy();
    expect(screen.getByText("已忽略")).toBeTruthy();
  });

  it("关掉的线程：不能追问、不给「关掉」，但还能收卡（后端 keep_card 不判 status）", () => {
    const props = draw({ threads: [thread({ status: "closed", cards: [card()] })] });

    expect(screen.getByText("已关掉")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "说这一轮" })).toBeNull();
    expect(screen.queryByRole("button", { name: "关掉这条" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "收下为下一轮输入" }));
    expect(props.onKeep).toHaveBeenCalledWith("d-1", "C1", "seed");
    expect(props.onClose).not.toHaveBeenCalled();
  });

  it("聊满 30 条之后不再点亮「说这一轮」（T4' 上限，M18 从 12 放宽）", () => {
    draw({
      threads: [
        thread({
          turns: Array.from({ length: 29 }, (_, i) => ({
            index: i,
            role: i % 2 === 0 ? "user" : "assistant",
            text: `第${i}条`,
            based_on: [],
            author: i % 2 === 0 ? "user" : "assistant",
            round: 1,
          })),
        }),
      ],
    });

    expect(screen.getByText(/已经聊满/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "说这一轮" })).toBeNull();
  });

  it("有动作在途时全部点不动（防连点）", () => {
    const props = draw({
      threads: [thread({ turns: [turn(0, "user"), turn(1, "assistant")], cards: [card()] })],
      pending: true,
    });

    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "先只做全量" } });
    expect((screen.getByRole("button", { name: "开跑" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "总结成卡" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole("button", { name: "关掉这条" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole("button", { name: "收下为材料" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect((screen.getByRole("button", { name: "忽略" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
    expect(props.onClose).not.toHaveBeenCalled();
  });
});

describe("DiscussionTray · 顶部发起调研（M17：从灵感箱底部搬过来）", () => {
  function runningExploration() {
    return {
      exploration_id: "x-1",
      project_id: "p1",
      kind: "inquiry",
      status: "running",
      scope_kind: "document",
      scope_ref: null,
      scope_text: null,
      created_at: "2026-10-05T10:00:00+00:00",
      failure_reason: null,
      abandon_reason: null,
      overflow_count: 0,
      candidates: [],
    } as never;
  }

  it("D1 点「发起调研（整份文档）」把请求交出去", () => {
    const props = draw({ onStartResearch: vi.fn() });

    fireEvent.click(screen.getByRole("button", { name: "发起调研（整份文档）" }));
    expect(props.onStartResearch).toHaveBeenCalledTimes(1);
  });

  it("D2 有调研在跑：按钮禁用 + 照实说只允许一趟", () => {
    draw({
      onStartResearch: vi.fn(),
      explorations: [runningExploration()],
      explorationsPending: false,
    });

    expect(
      (screen.getByRole("button", { name: "发起调研（整份文档）" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText(/同一项目同时只允许一趟调研/)).toBeTruthy();
  });

  it("D3 没传 onStartResearch 就不出这一行（旧调用方不受影响）", () => {
    draw({});

    expect(screen.queryByRole("button", { name: "发起调研（整份文档）" })).toBeNull();
  });
});

describe("DiscussionTray · panel 自主跑（M18）", () => {
  function panelThread(over: Partial<ThreadView> = {}): ThreadView {
    return thread({
      participants: [
        { role_id: "dlg.challenger", role_title: "反方", joined_round: 1 },
        { role_id: "dlg.user_voice", role_title: "用户之声", joined_round: 1 },
      ],
      ...over,
    });
  }

  function summary(index: number) {
    return { index, agreed: [`共识${index}`], disagreed: [] as string[], open: [] as string[] };
  }

  it("开跑后自动跑到 N 轮就停（轮数选 1，跑一轮）", async () => {
    const done = panelThread({ summaries: [summary(1)] });
    const onRunRound = vi.fn().mockResolvedValue(done);
    const onStartPanel = vi.fn().mockResolvedValue("d-1");
    draw({ threads: [panelThread({})], onStartPanel, onRunRound });

    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "日报要不要日报" } });
    fireEvent.change(screen.getByLabelText("跑几轮"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "开跑" }));

    await waitFor(() => expect(onStartPanel).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onRunRound).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText(/正在跑/)).toBeNull());
  });

  it("点停止：调 abort，不再调下一轮", async () => {
    let resolveRun: (view: ThreadView) => void = () => undefined;
    const onRunRound = vi
      .fn()
      .mockImplementation(
        () => new Promise<ThreadView>((resolve) => { resolveRun = resolve; }),
      );
    const onAbortRun = vi.fn();
    const onStartPanel = vi.fn().mockResolvedValue("d-1");
    draw({ threads: [panelThread({})], onStartPanel, onRunRound, onAbortRun });

    fireEvent.change(screen.getByLabelText("讨论主题"), { target: { value: "日报要不要日报" } });
    fireEvent.change(screen.getByLabelText("跑几轮"), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "开跑" }));
    await waitFor(() => expect(onRunRound).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "停止" }));
    expect(onAbortRun).toHaveBeenCalledWith("d-1");
    resolveRun(panelThread({}));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(onRunRound).toHaveBeenCalledTimes(1);
  });

  it("再跑一轮：从当前综合数继续跑一轮", async () => {
    const done = panelThread({ summaries: [summary(1), summary(2)] });
    const onRunRound = vi.fn().mockResolvedValue(done);
    draw({ threads: [panelThread({ summaries: [summary(1)] })], onRunRound });

    fireEvent.click(screen.getByRole("button", { name: "再跑一轮" }));

    await waitFor(() => expect(onRunRound).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryByText(/正在跑/)).toBeNull());
  });

  it("综合按轮显示：共识/分歧/未决", () => {
    draw({
      threads: [
        panelThread({
          summaries: [{ index: 1, agreed: ["都认要写"], disagreed: ["怎么写"], open: ["给谁看"] }],
        }),
      ],
    });

    expect(screen.getByText(/第 1 轮/)).toBeTruthy();
    expect(screen.getByText(/共识：都认要写/)).toBeTruthy();
    expect(screen.getByText(/分歧：怎么写/)).toBeTruthy();
    expect(screen.getByText(/未决：给谁看/)).toBeTruthy();
  });

  it("拉人进来：把线程 id 与选中的角色交出去", () => {
    const props = draw({ threads: [panelThread({})] });

    fireEvent.change(screen.getByLabelText(/拉人进来/), { target: { value: "dlg.analogist" } });
    fireEvent.click(screen.getByRole("button", { name: "拉进来" }));

    expect(props.onJoin).toHaveBeenCalledWith("d-1", "dlg.analogist");
  });

  it("在场的人不在拉人下拉框里", () => {
    draw({ threads: [panelThread({})] });

    const select = screen.getByLabelText(/拉人进来/) as HTMLSelectElement;
    const values = [...select.options].map((option) => option.value);
    expect(values).toEqual(["dlg.analogist"]);
  });
});
