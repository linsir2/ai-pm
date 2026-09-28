import { describe, expect, it } from "vitest";

import type { DroppedView, SeatPacketView } from "../api/types";
import { dropHeadline, dropsFor, unplacedDrops } from "./dropped";

const packet = {
  seat_id: "seat.data_scale",
  title: "数据规模与性能",
  status: "completed",
  pass_index: 2,
  basis: "读了 s5/s8 与 E1–E4",
  opinions: [{ local_id: "O1" }, { local_id: "O2" }, { local_id: "O3" }],
  judgment_count: 2,
  question_count: 1,
} as unknown as SeatPacketView;

const dropped: DroppedView[] = [
  {
    handle: "seat.data_scale:O2",
    seat_id: "seat.data_scale",
    pass_index: 2,
    reason: "与文档已有内容重复",
    source: "integrator",
  },
  {
    handle: "seat.data_scale:O1",
    seat_id: "seat.data_scale",
    pass_index: 1,
    reason: "上一趟的事",
    source: "integrator",
  },
  {
    handle: "查不到:O9",
    seat_id: null,
    pass_index: 2,
    reason: "归因对不上号",
    source: "assembly",
  },
];

describe("dropsFor", () => {
  it("只认同一席位、同一趟的留痕", () => {
    expect(dropsFor(packet, dropped).map((item) => item.handle)).toEqual([
      "seat.data_scale:O2",
    ]);
  });

  it("挂不到本趟席位包上的（更早那趟 / 地址对不上）要单独摆出来，不许静默消失", () => {
    expect(unplacedDrops(dropped, [packet]).map((item) => item.handle)).toEqual([
      "seat.data_scale:O1",
      "查不到:O9",
    ]);
  });

  it("它说的 3 条里 1 条没进卡片", () => {
    expect(dropHeadline(packet, dropsFor(packet, dropped))).toBe(
      "它说的 3 条里，1 条没进卡片",
    );
  });

  it("一条都没丢就不出这行", () => {
    expect(dropHeadline(packet, [])).toBe("");
  });
});
