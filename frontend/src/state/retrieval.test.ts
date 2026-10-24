import { describe, expect, it } from "vitest";

import type { RetrievalView, RoundView, SeatPacketView } from "../api/types";
import {
  materialSource,
  retrievalForPacket,
  retrievalForStage,
  retrievalLine,
} from "./retrieval";

function trace(over: Partial<RetrievalView> = {}): RetrievalView {
  return {
    stage: "seat",
    pass_index: 1,
    query: "导出权限",
    hit_count: 3,
    selected_count: 2,
    chunk_ids: ["m1#c00", "m1#c01"],
    ok: true,
    error: null,
    created_at: null,
    ...over,
  };
}

function roundWith(traces: RetrievalView[]): RoundView {
  return { retrievals: traces } as RoundView;
}

function packet(passIndex: number): SeatPacketView {
  return { pass_index: passIndex } as SeatPacketView;
}

describe("retrievalForStage / retrievalForPacket", () => {
  it("按 stage + pass 精确匹配，不拿错趟", () => {
    const round = roundWith([
      trace({ stage: "seed", pass_index: 0 }),
      trace({ stage: "seat", pass_index: 1 }),
      trace({ stage: "seat", pass_index: 2 }),
    ]);

    expect(retrievalForStage(round, "seed", 0)?.stage).toBe("seed");
    expect(retrievalForPacket(round, packet(2))?.pass_index).toBe(2);
    expect(retrievalForPacket(round, packet(3))).toBeNull();
  });

  it("整合按 integration_seq 取本次那条，不拿旧 pass 冒充", () => {
    const round = roundWith([
      trace({ stage: "integrate", pass_index: 1 }),
      trace({ stage: "integrate", pass_index: 2 }),
    ]);

    expect(retrievalForStage(round, "integrate", 2)?.pass_index).toBe(2);
    expect(retrievalForStage(round, "integrate", 3)).toBeNull();
  });

  it("没有 retrievals 字段（老轮次）时不炸", () => {
    expect(retrievalForStage({} as RoundView, "seed", 0)).toBeNull();
  });
});

describe("retrievalLine", () => {
  it("成功：命中 / 带进", () => {
    expect(retrievalLine(trace())).toBe("命中 3 块 / 带进 2 块");
  });

  it("失败：照实说原因", () => {
    expect(retrievalLine(trace({ ok: false, error: "检索器超时" }))).toBe(
      "检索失败：检索器超时",
    );
  });
});

describe("materialSource", () => {
  it("手勾优先；有检索记录才说自动；老轮不猜", () => {
    expect(materialSource("m1", ["m1"], true)).toBe("手勾");
    expect(materialSource("m2", [], true)).toBe("自动");
    expect(materialSource("m3", [], false)).toBeNull();
  });

  it("联网结果不标手勾/自动（它走另一条渲染路）", () => {
    expect(materialSource("w1", ["w1"], true, "web_search")).toBeNull();
  });
});
