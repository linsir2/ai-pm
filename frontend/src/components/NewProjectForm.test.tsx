// @vitest-environment jsdom
/* 首页 Word/WPS 化：左导航壳 + 上画廊（模板点选） + 下密行。
   新建表单抽成一份（两处共用）：画廊 + 名输入 + 提交，空模板表时退化成纯表单。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { TemplateView } from "../api/types";
import { NewProjectForm } from "./NewProjectForm";

afterEach(cleanup);

function template(
  template_id: string,
  title: string,
  chapters: number,
): TemplateView {
  return {
    template_id,
    title,
    sections: Array.from({ length: chapters }, (_, index) => ({
      section_id: `s${index + 1}`,
      title: `章${index + 1}`,
      purpose: "摆设",
      role: "plain",
      slots: [],
    })),
  };
}

const TEMPLATES = [template("standard", "标准版", 13), template("light", "轻量版", 6)];

it("画廊列出模板名与章节数，点选切换，提交带出(名, 模板id)", () => {
  const onCreate = vi.fn();
  render(
    <NewProjectForm templates={TEMPLATES} onCreate={onCreate} pending={false} error={null} />,
  );

  expect(screen.getByText("标准版")).toBeTruthy();
  expect(screen.getByText("13 章")).toBeTruthy();
  expect(screen.getByText("轻量版")).toBeTruthy();
  expect(screen.getByText("6 章")).toBeTruthy();

  fireEvent.click(screen.getByText("轻量版"));
  fireEvent.change(screen.getByPlaceholderText(/项目名/), { target: { value: "新产品" } });
  fireEvent.click(screen.getByRole("button", { name: "建项目" }));

  expect(onCreate).toHaveBeenCalledWith("新产品", "light");
});

it("默认选中标准版（不点就按 standard 建）", () => {
  const onCreate = vi.fn();
  render(
    <NewProjectForm templates={TEMPLATES} onCreate={onCreate} pending={false} error={null} />,
  );

  fireEvent.change(screen.getByPlaceholderText(/项目名/), { target: { value: "新产品" } });
  fireEvent.click(screen.getByRole("button", { name: "建项目" }));

  expect(onCreate).toHaveBeenCalledWith("新产品", "standard");
});

it("模板表为空时退化成纯表单（老后端不断粮）", () => {
  const onCreate = vi.fn();
  render(
    <NewProjectForm templates={[]} onCreate={onCreate} pending={false} error={null} />,
  );

  expect(screen.queryByText("标准版")).toBeNull();
  fireEvent.change(screen.getByPlaceholderText(/项目名/), { target: { value: "新产品" } });
  fireEvent.click(screen.getByRole("button", { name: "建项目" }));

  expect(onCreate).toHaveBeenCalledWith("新产品", "standard");
});

it("空名禁提交，失败横幅照常显示", () => {
  const onCreate = vi.fn();
  render(
    <NewProjectForm
      templates={TEMPLATES}
      onCreate={onCreate}
      pending={false}
      error="建项目失败"
    />,
  );

  expect(
    (screen.getByRole("button", { name: "建项目" }) as HTMLButtonElement).disabled,
  ).toBe(true);
  expect(screen.getByText("建项目失败")).toBeTruthy();
});

it("每张模板卡有名 + about 行", () => {
  const light: TemplateView = {
    template_id: "light",
    title: "轻量版",
    sections: [
      { section_id: "s1", title: "背景", purpose: "摆设", role: "backbone", slots: [] },
      { section_id: "s5", title: "功能", purpose: "摆设", role: "feature", slots: [] },
    ],
  };
  const onCreate = vi.fn();
  render(
    <NewProjectForm templates={[light]} onCreate={onCreate} pending={false} error={null} />,
  );

  // 需求变更：旧格式把 role 斜杠连排（标准版会渲染成六个"骨架章"重复六遍）。
  // 现在骨架计数、其余去重——见 templates/about.ts。
  const about = screen.getByText("2 章 · 1 骨架 · 功能需求");
  expect(about).toBeTruthy();
  expect(about.textContent ?? "").toContain("2 章 · 1 骨架 · 功能需求");
});
