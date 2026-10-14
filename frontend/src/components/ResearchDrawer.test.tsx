// @vitest-environment jsdom
/* 发起调研的抽屉（M8）：锚点说清"调研什么"，开始 / 取消各走各的回调。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResearchDrawer } from "./ResearchDrawer";

afterEach(cleanup);

function draw(over: Partial<Parameters<typeof ResearchDrawer>[0]> = {}) {
  const props = {
    anchor: { kind: "entry", ref: "e1", focus: "导出要有筛选" } as const,
    pending: false,
    onStart: vi.fn(),
    onCancel: vi.fn(),
    ...over,
  };
  render(<ResearchDrawer {...props} />);
  return props;
}

describe("ResearchDrawer", () => {
  it("条目锚点：原文预览与条目 id 都看得见", () => {
    draw();

    expect(screen.getByText("发起调研")).toBeTruthy();
    expect(screen.getByText("导出要有筛选")).toBeTruthy();
    expect(screen.getByText("e1")).toBeTruthy();
  });

  it("整份文档锚点：说清调研范围（不假装有原文）", () => {
    draw({ anchor: { kind: "document" } });

    expect(screen.getByText(/整份文档/)).toBeTruthy();
  });

  it("标题 id 是 research-title（别撞已有的 drawer-title）", () => {
    draw();

    expect(document.getElementById("research-title")).toBeTruthy();
  });

  it("点开始调研 / 取消，各调各的回调", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("button", { name: "开始调研" }));
    expect(props.onStart).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it("正在发起时主按钮不可点（防连点两次）", () => {
    draw({ pending: true });

    const button = screen.getByRole("button", { name: /调研/ }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
  });

  it("超长选段：全文照显示，并说明只取前 500 字发往后端（防静默截断）", () => {
    draw({ anchor: { kind: "entry", ref: "e1", focus: `导${"出".repeat(600)}` } });

    expect(screen.getByText(/仅前 500 字作补充说明/)).toBeTruthy();
  });

  it("500 字以内不显示截断说明", () => {
    draw();

    expect(screen.queryByText(/仅前 500 字/)).toBeNull();
  });
});
