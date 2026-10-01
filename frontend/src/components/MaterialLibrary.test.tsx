// @vitest-environment jsdom
/* 材料库面板（P19）：项目里全部材料 + 三个动作。

   三件事必须一眼分清：
   1. **带进这一轮**改的是本轮选择，**不动库**；
   2. **删掉**只有"没被用过"的才点得动（用过的那份写着是哪几个编号）；
   3. 联网搜回来的没有"重解析"（它是当时的网页快照，不是文件）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MaterialSummaryView } from "../api/types";
import { MaterialLibrary } from "./MaterialLibrary";

afterEach(cleanup);

const uploaded: MaterialSummaryView = {
  material_id: "m1",
  origin: "upload",
  title: "需求.md",
  source_ref: "attachment:需求.md",
  media_type: "text/markdown",
  bytes_len: 100,
  chunk_count: 3,
  chars: 1200,
  parser_id: "local-text",
  parser_version: "v1",
  truncated: false,
  warnings: [],
  stale: false,
  eids: [],
};

const used: MaterialSummaryView = { ...uploaded, material_id: "m2", title: "旧规范.pdf", eids: ["E4", "E9"] };
const searched: MaterialSummaryView = {
  ...uploaded,
  material_id: "m3",
  title: "权限模型详解",
  origin: "web_search",
  parser_id: "tavily",
};

function draw(over: Partial<Parameters<typeof MaterialLibrary>[0]> = {}) {
  const props = {
    materials: [uploaded, used, searched],
    selected: [] as string[],
    pending: false,
    onToggleSelected: vi.fn(),
    onDelete: vi.fn(),
    onReparse: vi.fn(),
    onCollapse: vi.fn(),
    ...over,
  };
  render(<MaterialLibrary {...props} />);
  return props;
}

describe("MaterialLibrary", () => {
  it("列全部材料：来源 / 块数 / 被哪些编号引用过", () => {
    draw();

    expect(screen.getByText("需求.md")).toBeTruthy();
    expect(screen.getByText(/来源：联网检索/)).toBeTruthy();
    expect(screen.getAllByText(/3 块/)).toHaveLength(3);   // 三份材料都写着块数
    expect(screen.getByText(/已被引用：E4、E9/)).toBeTruthy();
  });

  it("被引用过的：删按钮禁用，并说清为什么", () => {
    draw();

    const disabled = screen.getAllByRole("button", { name: /删掉/ }).filter(
      (button) => (button as HTMLButtonElement).disabled,
    );
    expect(disabled).toHaveLength(1);
    expect(screen.getByText(/已经发过编号 E4、E9，不能删/)).toBeTruthy();
  });

  it("联网来的材料没有重解析按钮", () => {
    draw({ materials: [searched] });

    expect(screen.queryByRole("button", { name: /重解析/ })).toBeNull();
  });

  it("带进这一轮与从库里删掉是两个动作", () => {
    const props = draw({ materials: [uploaded] });

    fireEvent.click(screen.getByRole("button", { name: /带进这一轮/ }));
    expect(props.onToggleSelected).toHaveBeenCalledWith("m1");
    expect(props.onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /删掉 需求.md/ }));
    expect(props.onDelete).toHaveBeenCalledWith("m1");
  });

  it("已经带进这一轮的那份写着'不带进这一轮'", () => {
    const props = draw({ materials: [uploaded], selected: ["m1"] });

    fireEvent.click(screen.getByRole("button", { name: /不带进这一轮/ }));

    expect(props.onToggleSelected).toHaveBeenCalledWith("m1");
  });

  it("空库时说实话，不摆一个空壳", () => {
    draw({ materials: [] });

    expect(screen.getByText(/还没有材料/)).toBeTruthy();
  });
});
