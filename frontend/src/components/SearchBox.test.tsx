// @vitest-environment jsdom
/* 顶栏搜索框（F6）：纯展示组件——打字把查询递上去，点一条把命中递上去。
   取数（防抖 + 调后端）在 `useWorkspaceData` 里，组件只管画。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { SearchResultsView } from "../api/types";
import { SearchBox } from "./SearchBox";

afterEach(cleanup);

const results: SearchResultsView = {
  query: "导出",
  truncated: false,
  hits: [
    { kind: "entry", label: "按时间筛选导出", ref: "en-1", where: "s5/order-export.one_liner" },
    { kind: "opinion", label: "导出的量级要先定", ref: "r1", where: "seat.data_scale" },
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

  it("截断了就说出来；查询为空时什么都不弹", () => {
    draw({ query: "导出", results: { ...results, truncated: true } });
    expect(screen.getByText(/只显示前/)).toBeTruthy();

    cleanup();
    draw({ query: "   ", results: { query: "   ", hits: [], truncated: false } });
    expect(screen.queryByRole("list")).toBeNull();
  });
});
