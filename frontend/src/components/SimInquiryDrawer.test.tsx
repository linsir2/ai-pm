// @vitest-environment jsdom
/* 模拟抽屉（M10）：假设卡逐条勾选收下，默认全空；提交时只交勾中的 id。

   钉三件事：① 假设/盲区默认不勾；② 空选择时收下不可点；③ pending 时全不可点。
   切片是分组（不可勾），只起展示作用。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SimInquiryView } from "../api/types";
import { SimInquiryDrawer } from "./SimInquiryDrawer";

afterEach(cleanup);

const inquiry: SimInquiryView = {
  slices: [
    {
      local_id: "S1",
      situation: "运营月底要对账",
      job: "按时间筛出订单",
      trigger: "月底结账",
      workaround: "导出全部再手工筛",
      anchors: ["E1"],
    },
  ],
  hypotheses: [
    {
      local_id: "H1",
      slice_id: "S1",
      statement: "运营宁愿等一分钟也不愿分两次导",
      grounding: "document",
      based_on: ["E1"],
      falsification: "观察到运营主动分多次小批量导出",
      evidence_needed: "跟两场月底结账看实际操作",
    },
  ],
  unknowns: [
    {
      local_id: "U1",
      statement: "节假日前的导出量会不会爆",
      why_it_matters: "决定要不要做异步导出",
      how_to_find_out: "拉去年节假日的导出日志",
    },
  ],
  runs: 1,
  chosen: [],
};

function draw(over: Partial<Parameters<typeof SimInquiryDrawer>[0]> = {}) {
  const props = {
    inquiry,
    pending: false,
    initialChosen: [] as string[],
    onKeep: vi.fn(),
    onClose: vi.fn(),
    ...over,
  };
  render(<SimInquiryDrawer {...props} />);
  return props;
}

describe("SimInquiryDrawer", () => {
  it("假设与盲区默认不勾，勾了才调 onKeep", () => {
    const onKeep = vi.fn();
    draw({ onKeep });

    expect(screen.getByText("运营宁愿等一分钟也不愿分两次导")).toBeTruthy();
    expect(screen.getByText("节假日前的导出量会不会爆")).toBeTruthy();
    const keep = screen.getByRole("button", { name: /收下勾选/ });
    expect((keep as HTMLButtonElement).disabled).toBe(true);

    const toggles = screen.getAllByRole("button", { name: "勾选" });
    expect(toggles.length).toBe(2);
    fireEvent.click(toggles[0]!);
    fireEvent.click(screen.getByRole("button", { name: /收下勾选/ }));
    expect(onKeep).toHaveBeenCalledWith(["H1"]);
  });

  it("pending 时全不可点", () => {
    const onKeep = vi.fn();
    draw({ pending: true, onKeep });

    expect(
      (screen.getByRole("button", { name: /收下勾选/ }) as HTMLButtonElement).disabled,
    ).toBe(true);
    const toggles = screen.getAllByRole("button", { name: "勾选" });
    fireEvent.click(toggles[0]!);
    expect(onKeep).not.toHaveBeenCalled();
  });

  it("上次勾过的默认勾上（刷新回来不丢选择）", () => {
    const onKeep = vi.fn();
    draw({ initialChosen: ["U1"], onKeep });

    fireEvent.click(screen.getByRole("button", { name: /收下勾选/ }));
    expect(onKeep).toHaveBeenCalledWith(["U1"]);
  });

  it("还没跑出来时显示推演中/失败 copy，不崩", () => {
    draw({ inquiry: null, pending: true, initialChosen: [] });
    expect(screen.getByText(/正在推演/)).toBeTruthy();
  });
});
