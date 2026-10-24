// @vitest-environment jsdom
/* 顶栏搜索框（F6）：纯展示组件——打字把查询递上去，点一条把命中递上去。
   取数（防抖 + 调后端）在 `useWorkspaceData` 里，组件只管画。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SearchHitAction } from "../api/payload";
import type { SearchHitView, SearchResultsView } from "../api/types";
import { SearchBox } from "./SearchBox";

afterEach(cleanup);

const results: SearchResultsView = {
  query: "导出",
  truncated: false,
  hits: [
    { kind: "entry", label: "按时间筛选导出", ref: "en-1", where: "s5/order-export.one_liner" },
    { kind: "opinion", label: "导出的量级要先定", ref: "r1", where: "seat.data_scale" },
    {
      kind: "material",
      label: "诊断报告.pdf：…结论：导出要走异步队列…",
      ref: "m1#c03",
      where: "材料 · 第 3 页 §2.1",
    },
  ],
};

function draw(over: Partial<Parameters<typeof SearchBox>[0]> = {}) {
  const props = {
    query: "",
    results: null as SearchResultsView | null,
    pending: false,
    onQueryChange: vi.fn(),
    onPick: vi.fn(),
    ...over,
  };
  render(<SearchBox {...props} />);
  return props;
}

describe("SearchBox", () => {
  it("打字把查询递出去", () => {
    const props = draw();

    fireEvent.change(screen.getByLabelText("搜索"), { target: { value: "导出" } });

    expect(props.onQueryChange).toHaveBeenCalledWith("导出");
  });

  it("有结果就列出，点一条把命中递出去", () => {
    const props = draw({ query: "导出", results });

    fireEvent.click(screen.getByText("按时间筛选导出"));

    expect(props.onPick).toHaveBeenCalledWith(results.hits[0]);
  });

  it("命中落点写在那一行上（去哪一章 / 哪个席位）", () => {
    draw({ query: "导出", results });

    expect(screen.getByText(/s5\/order-export\.one_liner/)).toBeTruthy();
    expect(screen.getByText(/seat\.data_scale/)).toBeTruthy();
  });

  it("材料那一类也有中文名（不许把 kind 原样甩出来）", () => {
    draw({ query: "异步队列", results });

    expect(screen.getByText("材料")).toBeTruthy();
    expect(screen.getByText(/诊断报告\.pdf/)).toBeTruthy();
  });

  it("截断了就说出来；查询为空时什么都不弹", () => {
    draw({ query: "导出", results: { ...results, truncated: true } });
    expect(screen.getByText(/只显示前/)).toBeTruthy();

    cleanup();
    draw({ query: "   ", results: { query: "   ", hits: [], truncated: false } });
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("键盘：↓ 选中第一条，Enter 把命中递出去", () => {
    const props = draw({ query: "导出", results });
    const input = screen.getByLabelText("搜索");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(props.onPick).toHaveBeenCalledWith(results.hits[0]);
  });

  it("键盘：↓↓ 到第二条，Enter 递第二条", () => {
    const props = draw({ query: "导出", results });
    const input = screen.getByLabelText("搜索");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(props.onPick).toHaveBeenCalledWith(results.hits[1]);
  });

  it("键盘：↑ 在第一条上不越界；没选中时 Enter 不误发", () => {
    const props = draw({ query: "导出", results });
    const input = screen.getByLabelText("搜索");

    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.onPick).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(props.onPick).toHaveBeenCalledWith(results.hits[0]);
  });
});

it("带进本轮：显示次按钮，点了把命中递给回调", () => {
  const onAdd = vi.fn();
  const actionFor = (hit: SearchHitView): SearchHitAction =>
    hit.kind === "material"
      ? { visible: true, enabled: true, label: "带进本轮" }
      : { visible: false };
  draw({
    query: "导出",
    results,
    actionFor,
    onAddToRound: onAdd,
  });

  fireEvent.click(screen.getByRole("button", { name: "带进本轮" }));

  expect(onAdd).toHaveBeenCalledWith(results.hits[2]);
});

it("带进本轮：禁用时按钮不可点，理由在 title 里", () => {
  const actionFor = (hit: SearchHitView): SearchHitAction =>
    hit.kind === "material"
      ? {
          visible: true,
          enabled: false,
          label: "带进本轮",
          reason: "这一块已经在当前轮里了",
        }
      : { visible: false };
  draw({
    query: "导出",
    results,
    actionFor,
  });

  const button = screen.getByRole("button", { name: "带进本轮" });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(button.getAttribute("title")).toBe("这一块已经在当前轮里了");
});
