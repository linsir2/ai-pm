import { describe, expect, it } from "vitest";

import type { DroppedView, SeatPacketView } from "../api/types";
import { dropHeadline, dropsFor, toolDrops, unplacedDrops } from "./dropped";

const packet = {
  seat_id: "seat.data_scale",
  title: "数据规模与性能",
  status: "completed",
  pass_index: 2,
  basis: "读了 s5/s8 与 E1–E4",
  opinions: [{ local_id: "O1" }, { local_id: "O2" }, { local_id: "O3" }],
  judgment_count: 2,
  question_count: 1,
  packet_id: "pkt-r1-seat_review-seat.data_scale-p2a1",
} as unknown as SeatPacketView;

const dropped: DroppedView[] = [
  {
    handle: "seat.data_scale:O2",
    seat_id: "seat.data_scale",
    pass_index: 2,
    reason: "与文档已有内容重复",
    source: "integrator",
    packet_id: "pkt-r1-seat_review-seat.data_scale-p2a1",
  },
  {
    handle: "seat.data_scale:O1",
    seat_id: "seat.data_scale",
    pass_index: 1,
    reason: "上一趟的事",
    source: "integrator",
    packet_id: "pkt-r1-seat_review-seat.data_scale-p1a1",
  },
  {
    handle: "查不到:O9",
    seat_id: null,
    pass_index: 2,
    reason: "归因对不上号",
    source: "assembly",
  },
  {
    handle: "联网检索 RBAC 权限模型",
    seat_id: null,
    pass_index: 1,
    reason: "检索失败：联网搜索失败：HTTP 504",
    source: "tool",
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
      "联网检索 RBAC 权限模型",
    ]);
  });

  it("工具那几笔（检索失败 / 没带进去）单独拎出来：它们不是「去处写错的」", () => {
    expect(toolDrops(dropped).map((item) => item.handle)).toEqual([
      "联网检索 RBAC 权限模型",
    ]);
  });

  it("带 seat_id 的 TOOL 留痕（每方超名额）只挂在席位包下，不进工具段", () => {
    const seatTool: DroppedView = {
      handle: "联网申请 第四条",
      seat_id: "seat.data_scale",
      pass_index: 2,
      reason: "每方最多 3 条联网申请，多出来的这一条已丢弃",
      source: "tool",
      packet_id: packet.packet_id ?? undefined,
    };
    expect(toolDrops([...dropped, seatTool]).map((item) => item.handle)).toEqual([
      "联网检索 RBAC 权限模型",
    ]);
    expect(dropsFor(packet, [seatTool]).map((item) => item.handle)).toEqual([
      "联网申请 第四条",
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


describe("dropsFor 按 packet_id 精确匹配", () => {
  const withId = {
    ...packet,
    packet_id: "pkt-r1-seat_review-seat.data_scale-p2a1",
  } as unknown as SeatPacketView;

  it("有 packet_id 就按 id 匹配，即使 pass_index 不同", () => {
    // 同一包的另一趟（pass_index 不同）——按 id 仍然匹配
    const otherPass = {
      handle: "seat.data_scale:O9",
      seat_id: "seat.data_scale",
      pass_index: 9,
      reason: "同一包里的另一条",
      source: "integrator",
      packet_id: "pkt-r1-seat_review-seat.data_scale-p2a1",
    };
    expect(dropsFor(withId, [...dropped, otherPass]).map((item) => item.handle)).toContain(
      "seat.data_scale:O9",
    );
  });

  it("不同 packet_id 的留痕不匹配（即使 seat_id 与 pass_index 相同）", () => {
    const differentPacket = {
      handle: "seat.data_scale:O7",
      seat_id: "seat.data_scale",
      pass_index: 2,
      reason: "另一包里的",
      source: "integrator",
      packet_id: "pkt-r1-seat_review-seat.data_scale-p1a1",
    };
    expect(dropsFor(withId, [...dropped, differentPacket])).not.toContainEqual(
      expect.objectContaining({ handle: "seat.data_scale:O7" }),
    );
  });

  it("packet_id 缺失时回退 (seat_id, pass_index) 启发式", () => {
    const legacyPacket = { ...packet, packet_id: "" } as unknown as SeatPacketView;
    const legacyDrop: DroppedView = {
      handle: "seat.data_scale:O3",
      seat_id: "seat.data_scale",
      pass_index: 2,
      reason: "老数据没有 packet_id",
      source: "integrator",
    };
    expect(dropsFor(legacyPacket, [...dropped, legacyDrop]).map((item) => item.handle)).toContain(
      "seat.data_scale:O3",
    );
  });

  it("assembly 与 tool 来源（无 packet_id）不会被误挂到席位包下", () => {
    const onlyTool = dropped.filter((item) => item.source === "tool");
    expect(dropsFor(withId, onlyTool)).toEqual([]);
  });
});

describe("unplacedDrops 与 dropsFor 用同一套匹配", () => {
  it("有 packet_id 的留痕挂得上，不会落进 unplaced", () => {
    const placed = dropped.filter((item) => item.packet_id === packet.packet_id);
    expect(unplacedDrops(placed, [packet])).toEqual([]);
  });

  it("无 packet_id 的老数据按启发式判断", () => {
    const legacy: DroppedView = {
      handle: "seat.data_scale:O5",
      seat_id: "seat.data_scale",
      pass_index: 2,
      reason: "老数据",
      source: "integrator",
    };
    expect(unplacedDrops([legacy], [packet])).toEqual([]);
  });
});
