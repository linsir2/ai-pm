// @vitest-environment jsdom
/* 顶栏窄屏的收纳（`P29` F7）：文字进视觉隐藏，但**连接状态与可访问名都留着**。
   （视觉宽度那部分由 `bin/ui_shot.mjs` 的 `wrapped` 量测在真浏览器里验，这里只钉"没删东西"。） */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProjectSummaryView } from "../api/types";
import { TopBar } from "./TopBar";

afterEach(cleanup);

const project = {
  project_id: "p1",
  title: "订单中台",
  version_no: 3,
  updated_at: "2026-10-02T00:00:00+00:00",
  template_id: "standard",
  template_title: "标准版",
} as ProjectSummaryView;

function draw(connection: "open" | "lost" | "idle" = "open") {
  const props = { onSelectProject: vi.fn(), onExport: vi.fn(), onToggleTheme: vi.fn() };
  render(
    <TopBar
      projects={[project]}
      project={project}
      connection={connection}
      theme="light"
      {...props}
    />,
  );
  return props;
}

describe("TopBar", () => {
  it("连接状态是 role=status 的文字（窄屏只把它视觉隐藏，不删）", () => {
    draw("open");

    expect(screen.getByRole("status").textContent).toContain("推送已连上");
  });

  it("暗色按钮是有可访问名的开关（aria-pressed 跟着主题走）", () => {
    const props = draw();

    const button = screen.getByRole("button", { name: /暗色/ });
    expect(button.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(button);
    expect(props.onToggleTheme).toHaveBeenCalledTimes(1);
  });

  it("导出按钮可点（窄屏也不该被藏掉）", () => {
    const props = draw();

    fireEvent.click(screen.getByRole("button", { name: "导出" }));

    expect(props.onExport).toHaveBeenCalledTimes(1);
  });

  it("文档折叠开关：点调 onToggleDoc，aria-pressed 跟着 docOpen 走", () => {
    const props = {
      onSelectProject: vi.fn(),
      onExport: vi.fn(),
      onToggleTheme: vi.fn(),
      onToggleDoc: vi.fn(),
    };
    const { rerender } = render(
      <TopBar
        projects={[project]}
        project={project}
        connection="open"
        theme="light"
        docOpen={true}
        {...props}
      />,
    );

    const button = screen.getByRole("button", { name: "文档" });
    expect(button.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(button);
    expect(props.onToggleDoc).toHaveBeenCalledTimes(1);

    rerender(
      <TopBar
        projects={[project]}
        project={project}
        connection="open"
        theme="light"
        docOpen={false}
        {...props}
      />,
    );
    expect(screen.getByRole("button", { name: "文档" }).getAttribute("aria-pressed")).toBe(
      "false",
    );
  });

  it("没传折叠回调就不出按钮（列表页顶栏保持干净）", () => {
    draw();

    expect(screen.queryByRole("button", { name: "文档" })).toBeNull();
  });

  it("灵感箱入口只留页签一个（顶栏不再有第二个入口）", () => {
    render(
      <TopBar
        projects={[project]}
        project={project}
        connection="open"
        theme="light"
        onSelectProject={vi.fn()}
        onExport={vi.fn()}
        onToggleTheme={vi.fn()}
        libraryCount={3}
        onOpenLibrary={vi.fn()}
      />,
    );

    expect(screen.queryByText("灵感箱")).toBeNull();
    expect(screen.getByText("材料库")).toBeTruthy();
  });
});
