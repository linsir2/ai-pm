import { describe, expect, it } from "vitest";

import { initialState, reduce } from "./workspace";
import type { RoundView, WorkspaceEvent } from "../api/types";

const round = { round_id: "r1", step: "seats_running", seat_packets: [] } as unknown as RoundView;

describe("reduce", () => {
  it("loaded 覆盖整个工作区并记下项目 id", () => {
    const state = reduce(initialState, {
      type: "loaded",
      workspace: { project: { project_id: "p1" }, document: {}, round } as never,
    });
    expect(state.projectId).toBe("p1");
    expect(state.round?.round_id).toBe("r1");
  });

  it("round 动作只换这一轮（提交后的响应）", () => {
    const state = reduce(
      { ...initialState, projectId: "p1" },
      { type: "round", round: { ...round, waiting_on: "draft_card" } as never },
    );
    expect(state.round?.waiting_on).toBe("draft_card");
  });

  it("带 expectRoundId 的 round 回包：期望命中才落（迟到但对得上）", () => {
    const state = reduce(
      { ...initialState, projectId: "p1", round },
      {
        type: "round",
        round: { ...round, waiting_on: "draft_card" } as never,
        expectRoundId: "r1",
      },
    );
    expect(state.round?.waiting_on).toBe("draft_card");
  });

  it("带 expectRoundId 的 round 回包：新轮已落地时旧回包丢弃（不倒灌）", () => {
    const r2 = { ...round, round_id: "r2", waiting_on: "questionnaire" };
    const state = reduce(
      { ...initialState, projectId: "p1", round: r2 as never },
      {
        type: "round",
        round: { ...round, waiting_on: "draft_card" } as never,
        expectRoundId: "r1",
      },
    );
    expect(state.round?.round_id).toBe("r2");
    expect(state.round?.waiting_on).toBe("questionnaire");
  });

  it("packet.emitted 只把该席位状态改成 completed 并置 roundStale（opinions 真源在 GET /round）", () => {
    const base = reduce(
      { ...initialState, projectId: "p1" },
      {
        type: "loaded",
        workspace: {
          project: { project_id: "p1" },
          document: {},
          round: {
            ...round,
            seat_packets: [{ seat_id: "seat.data_scale", status: "pending", opinions: [] }],
          },
        } as never,
      },
    );

    const state = reduce(base, {
      type: "event",
      event: {
        name: "packet.emitted",
        project_id: "p1",
        round_id: "r1",
        payload: {
          seat_id: "seat.data_scale",
          packet_id: "pkt-r1-seat.seat.data_scale-1",
          basis: "读了 s5 与 E1",
          pass_index: 1,
        },
        occurred_at: "2026-09-22T10:00:00+00:00",
      } as never,
    });

    const packet = state.round!.seat_packets[0]!;
    expect(packet.status).toBe("completed");
    // 铁律④：事件不带 opinions，opinions 保持原样（空），等 GET /round 重读填充
    expect(packet.opinions).toEqual([]);
    expect(state.roundStale).toBe(true);
  });

  it("别的项目的事件不影响当前工作区", () => {
    const base = { ...initialState, projectId: "p1", round: round };
    const state = reduce(base, {
      type: "event",
      event: {
        name: "round.updated",
        project_id: "p2",
        round_id: "r9",
        payload: {},
      } as never,
    });
    expect(state).toBe(base);
  });

  it("doc.changed 只记一个「该刷新文档」的信号，不自己改文档", () => {
    const state = reduce(
      { ...initialState, projectId: "p1", round: round },
      {
        type: "event",
        event: {
          name: "doc.changed",
          project_id: "p1",
          round_id: "r1",
          payload: { version_no: 2 },
        } as never,
      },
    );
    expect(state.documentStale).toBe(true);
    expect(state.round).toBe(round);
  });

  it("doc.changed 里的 written_block_ids 拿去做高亮（不猜哪块是新写的）", () => {
    const state = reduce(
      { ...initialState, projectId: "p1", round },
      {
        type: "event",
        event: {
          name: "doc.changed",
          project_id: "p1",
          round_id: "r1",
          payload: { version_no: 2, written_block_ids: ["b-1", "b-2"] },
        } as never,
      },
    );
    expect(state.justWritten).toEqual(["b-1", "b-2"]);
  });

  it("round.updated 把进度就地记下来，但不改「该弹什么卡」以外的判断", () => {
    const state = reduce(
      { ...initialState, projectId: "p1", round },
      {
        type: "event",
        event: {
          name: "round.updated",
          project_id: "p1",
          round_id: "r1",
          payload: { step: "awaiting_answers", waiting_on: "questionnaire", seat_round_count: 2 },
        } as never,
      },
    );
    expect(state.round!.step).toBe("awaiting_answers");
    expect(state.round!.seat_round_count).toBe(2);
    // 题目还没取回来，所以这一轮不会凭事件弹出问卷（awaitingMode 会判成 loading）
    expect(state.round!.questions).toBeUndefined();
  });

  it("generation.failed 记下原因并且要重读这一轮（失败原因在领域里）", () => {
    const state = reduce(
      { ...initialState, projectId: "p1", round },
      {
        type: "event",
        event: {
          name: "generation.failed",
          project_id: "p1",
          round_id: "r1",
          payload: { reason: "模型复述时改了原话" },
        } as never,
      },
    );
    expect(state.roundStale).toBe(true);
    expect(state.error).toBe("模型复述时改了原话");
  });

  it("document 动作只换文档，并清掉重读信号", () => {
    const state = reduce(
      { ...initialState, projectId: "p1", documentStale: true },
      { type: "document", document: { version_no: 2 } as never },
    );
    expect(state.documentStale).toBe(false);
    expect(state.document?.version_no).toBe(2);
  });

  it("连接状态与错误提示各走各的，不互相清掉", () => {
    const lost = reduce(
      { ...initialState, error: "上一轮的报错" },
      { type: "connection", state: "lost" },
    );
    expect(lost.connection).toBe("lost");
    expect(lost.error).toBe("上一轮的报错");
  });

  it("reset 清工作区但保留连接状态", () => {
    const state = reduce(
      { ...initialState, projectId: "p1", round, connection: "open" },
      { type: "reset" },
    );
    expect(state.projectId).toBeNull();
    expect(state.round).toBeNull();
    expect(state.connection).toBe("open");
  });

  it("exploration.updated 只置「该重读探索列表」，轮次与文档一根手指都不碰", () => {
    const document = { version_no: 1 } as never;
    const state = reduce(
      { ...initialState, projectId: "p1", round, document },
      {
        type: "event",
        event: {
          name: "exploration.updated",
          project_id: "p1",
          round_id: null,
          payload: { exploration_id: "x-1", status: "running" },
        } as never,
      },
    );
    expect(state.explorationStale).toBe(true);
    expect(state.round).toBe(round);
    expect(state.document).toBe(document);
    expect(state.error).toBeNull();
  });

  it("别的项目的 exploration.updated 不影响当前工作区", () => {
    const base = { ...initialState, projectId: "p1", round };
    const state = reduce(base, {
      type: "event",
      event: {
        name: "exploration.updated",
        project_id: "p2",
        round_id: null,
        payload: { exploration_id: "x-9", status: "done" },
      } as never,
    });
    expect(state).toBe(base);
  });

  it("探索列表重读完之后清掉信号（不然第二趟的变化不会再触发重读）", () => {
    const state = reduce(
      { ...initialState, projectId: "p1", explorationStale: true },
      { type: "explorations" },
    );
    expect(state.explorationStale).toBe(false);
  });

  it("事件名不在五类里就当没发生", () => {
    const base = { ...initialState, projectId: "p1", round };
    const event = {
      name: "made.up",
      project_id: "p1",
      round_id: "r1",
      payload: {},
    } as unknown as WorkspaceEvent;
    expect(reduce(base, { type: "event", event }).round).toBe(round);
  });
});
