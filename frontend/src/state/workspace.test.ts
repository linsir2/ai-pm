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

  it("packet.emitted 让对应席位从等待变成已完成（闭环区逐条出现）", () => {
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
          basis: "读了 s5 与 E1",
          opinions: [
            {
              local_id: "O1",
              form: "judgment",
              about: "性能上限",
              claim: "60 秒内完成",
              because: "同类做法",
              eids: ["E1"],
              eid: "E2",
            },
          ],
        },
        occurred_at: "2026-09-22T10:00:00+00:00",
        seq: 4,
      } as never,
    });

    const packet = state.round!.seat_packets[0]!;
    expect(packet.status).toBe("completed");
    expect(packet.opinions[0]!.claim).toBe("60 秒内完成");
    expect(packet.judgment_count).toBe(1);
    expect(packet.question_count).toBe(0);
    expect(packet.basis).toBe("读了 s5 与 E1");
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
        seq: 5,
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
          seq: 6,
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
          seq: 7,
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
          seq: 8,
        } as never,
      },
    );
    expect(state.round!.step).toBe("awaiting_answers");
    expect(state.round!.seat_round_count).toBe(2);
    // 题目还没取回来，所以这一轮不会凭事件弹出问卷（awaitingMode 会判成 loading）
    expect(state.round!.questions).toBeUndefined();
  });

  it("重复送达的旧事件不再处理", () => {
    const base = { ...initialState, projectId: "p1", round, lastSeq: 9 };
    const state = reduce(base, {
      type: "event",
      event: {
        name: "doc.changed",
        project_id: "p1",
        round_id: "r1",
        payload: { version_no: 2 },
        seq: 4,
      } as never,
    });
    expect(state).toBe(base);
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
          seq: 9,
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

  it("事件名不在四类里就当没发生", () => {
    const base = { ...initialState, projectId: "p1", round };
    const event = {
      name: "made.up",
      project_id: "p1",
      round_id: "r1",
      payload: {},
      seq: 3,
    } as unknown as WorkspaceEvent;
    expect(reduce(base, { type: "event", event }).round).toBe(round);
  });
});
