// @vitest-environment jsdom
/* 输入框旁边的材料选择器（工具层 C-2）：
   选文件 → 真上传（onUpload）→ 列出来 → 能撤掉；撤掉后起轮体里没有它。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MaterialSummaryView } from "../api/types";
import { MaterialPicker } from "./MaterialPicker";

afterEach(cleanup);

const summary: MaterialSummaryView = {
  material_id: "m1",
  origin: "upload",
  title: "需求.md",
  source_ref: "attachment:需求.md",
  media_type: "text/markdown",
  bytes_len: 2048,
  chunk_count: 3,
  chars: 1200,
  parser_id: "local-text",
  parser_version: "v1",
  truncated: false,
  warnings: [],
  stale: false,
  eids: [],
};

function draw(over: Partial<Parameters<typeof MaterialPicker>[0]> = {}) {
  const props = {
    projectId: "p1",
    materials: [] as MaterialSummaryView[],
    pending: false,
    onUploaded: vi.fn(),
    onRemove: vi.fn(),
    ...over,
  };
  render(<MaterialPicker {...props} />);
  return props;
}

describe("MaterialPicker", () => {
  it("选文件就调 onUploaded（上传成功之后才进这一轮）", async () => {
    const uploaded = { ...summary };
    const onUploaded = vi.fn();
    const { container } = render(
      <MaterialPicker
        projectId="p1"
        materials={[]}
        pending={false}
        onUploaded={onUploaded}
        onRemove={() => undefined}
        uploader={async () => uploaded}
      />,
    );

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(["# 目标"], "需求.md", { type: "text/markdown" })] },
    });

    await vi.waitFor(() => expect(onUploaded).toHaveBeenCalledWith(uploaded));
  });

  it("已经选了的材料列出来：标题 + 块数 + 解析器", () => {
    draw({ materials: [summary] });

    expect(screen.getByText("需求.md")).toBeTruthy();
    expect(screen.getByText(/3 块/)).toBeTruthy();
    expect(screen.getByText(/local-text/)).toBeTruthy();
  });

  it("撤掉材料把 id 递出去", () => {
    const props = draw({ materials: [summary] });

    fireEvent.click(screen.getByRole("button", { name: /撤掉 需求.md/ }));

    expect(props.onRemove).toHaveBeenCalledWith("m1");
  });

  it("上传失败要说人话（API 的 message）", async () => {
    const { container } = render(
      <MaterialPicker
        projectId="p1"
        materials={[]}
        pending={false}
        onUploaded={() => undefined}
        onRemove={() => undefined}
        uploader={async () => {
          throw new Error("这份材料没有可抽取的文字：scan.pdf");
        }}
      />,
    );

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(["x"], "scan.pdf")] } });

    await vi.waitFor(() => expect(screen.getByRole("alert").textContent).toContain("没有可抽取的文字"));
  });

  it("材料页上要写清是**外部材料**，不是用户说的话", () => {
    draw({ materials: [summary] });

    expect(screen.getByText(/不是你说的话/)).toBeTruthy();
  });

  it("列表标出它是怎么来的：上传 / 联网检索（认不出的照实显示原值）", () => {
    draw({
      materials: [
        summary,
        { ...summary, material_id: "m2", title: "权限模型详解", origin: "web_search" },
        { ...summary, material_id: "m3", title: "怪东西", origin: "web_fetch" },
      ],
    });

    expect(screen.getByText(/来源：上传/)).toBeTruthy();
    expect(screen.getByText(/来源：联网检索/)).toBeTruthy();
    expect(screen.getByText(/来源：抓来的网页/)).toBeTruthy();
  });
});
