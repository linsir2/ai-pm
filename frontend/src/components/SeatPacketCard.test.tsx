// @vitest-environment jsdom
/* 席位包内留痕默认收（对话流纪律：包内 drops 与 LoopPane 的 unplaced 同规矩）。
   RIPR：fault=留痕全展回潮；R=render 到包，I=无交互，P=details 关闭态，R=断言结构。 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SeatPacketView } from "../api/types";
import { SeatPacketCard } from "./SeatPacketCard";

afterEach(cleanup);

describe("SeatPacketCard 留痕", () => {
  it("包内 drops 默认收：标题行露条数，明细仍在 DOM", () => {
    const { container } = render(
      <SeatPacketCard
        packet={
          {
            seat_id: "seat.a",
            title: "业务规则",
            status: "completed",
            pass_index: 1,
            packet_id: null,
            basis: "读了文档",
            opinions: [],
            judgment_count: 0,
            question_count: 0,
          } as never
        }
        order={1}
        running={false}
        drops={[
          { handle: "seat.a:O1", reason: "去处写错", source: "assembly", seat_id: "seat.a", pass_index: 1 },
          { handle: "seat.a:O2", reason: "没交代去向", source: "assembly", seat_id: "seat.a", pass_index: 1 },
        ]}
      />,
    );

    const folded = container.querySelector("details.drops-fold:not([open])");
    expect(folded).toBeTruthy();
    expect(folded!.querySelector("summary")!.textContent).toContain("2 条");
    expect(container.querySelectorAll("ul.drops li.drop")).toHaveLength(2);
  });

  it("没有 drops 就没有折叠行（防空标题）", () => {
    const { container } = render(
      <SeatPacketCard
        packet={
          {
            seat_id: "seat.a",
            title: "业务规则",
            status: "completed",
            pass_index: 1,
            packet_id: null,
            basis: "读了文档",
            opinions: [],
            judgment_count: 0,
            question_count: 0,
          } as never
        }
        order={1}
        running={false}
        drops={[]}
      />,
    );

    expect(container.querySelector("details.drops-fold")).toBeNull();
  });
});


describe("SeatPacketCard 跳过", () => {
  const base = {
    seat_id: "seat.a",
    title: "业务规则",
    status: "pending",
    pass_index: 1,
    packet_id: null,
    basis: "",
    opinions: [],
    judgment_count: 0,
    question_count: 0,
  } as SeatPacketView;

  it("可跳过时出按钮，点了把 seat_id 交出去（卡片自己不判断，只透传）", () => {
    const seen: string[] = [];
    const { container } = render(
      <SeatPacketCard
        packet={{ ...base, status: "pending" }}
        order={1}
        running={true}
        drops={[]}
        skippable={true}
        onSkip={(id) => seen.push(id)}
      />,
    );

    const button = container.querySelector("button.packet__skip");
    expect(button?.textContent).toContain("跳过");
    button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(seen).toEqual(["seat.a"]);
  });

  it("不可跳过就不出按钮（判断在 LoopPane 的 canSkipSeat，卡片只执行）", () => {
    const { container } = render(
      <SeatPacketCard
        packet={{ ...base, status: "pending" }}
        order={1}
        running={true}
        drops={[]}
        skippable={false}
        onSkip={() => {}}
      />,
    );

    expect(container.querySelector("button.packet__skip")).toBeNull();
  });

  it("没给 onSkip 也不出按钮（老调用方零改动）", () => {
    const { container } = render(
      <SeatPacketCard
        packet={{ ...base, status: "pending" }}
        order={1}
        running={true}
        drops={[]}
        skippable={true}
      />,
    );

    expect(container.querySelector("button.packet__skip")).toBeNull();
  });

  it("跳过态：stateText 是已跳过，留痕照常展开（跳过原因不能藏起来）", () => {
    const { container } = render(
      <SeatPacketCard
        packet={{ ...base, status: "skipped" }}
        order={1}
        running={true}
        drops={[
          {
            handle: "seat.a",
            reason: "用户跳过了席位 seat.a，本轮不再请它",
            source: "user_skipped",
            seat_id: "seat.a",
            pass_index: 1,
          },
        ]}
      />,
    );

    expect(container.querySelector(".packet__state")!.textContent).toContain("已跳过");
    expect(container.querySelector("button.packet__skip")).toBeNull();
    expect(container.querySelectorAll("ul.drops li.drop")).toHaveLength(1);
  });
});
