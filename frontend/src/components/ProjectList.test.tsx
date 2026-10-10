// @vitest-environment jsdom
/* 项目列表：一个项目 = 一行（标题 / 版本 / 时间），点它进那个项目的工作区。

   这一屏钉五件事：
   ① 每个项目一行，行上带**版本号与更新时间**——`TopBar` 那个 `<select>` 只给标题，
      看不出哪个项目进行到哪，那正是要补的；
   ② 点行进工作区（`onOpen` 收到的是那一行的 id）；
   ③ **一个项目都没有时不画空列表**，直接复用 `Onboarding`（空态只有一份）；
   ④ 新建表单：空白不提交、提交前去空白（与 `Onboarding` 同一口径）；
   ⑤ 加载中显示骨架，不显示空列表（否则"正在读"看起来像"没有项目"）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ProjectSummaryView } from "../api/types";
import { ProjectList } from "./ProjectList";

afterEach(cleanup);

function project(
  id: string,
  title: string,
  version_no: number,
  updated_at: string,
): ProjectSummaryView {
  return { project_id: id, title, version_no, updated_at };
}

const THREE = [
  project("p1", "订单中台", 3, "2026-10-09T14:23:00+08:00"),
  project("p2", "登录改版", 0, "2026-10-08T09:12:00+08:00"),
  project("p3", "数据看板", 1, "2026-10-01T20:05:00+08:00"),
];

function renderList(over: Partial<Parameters<typeof ProjectList>[0]> = {}) {
  const props = {
    projects: THREE,
    onOpen: vi.fn(),
    onCreate: vi.fn(),
    pending: false,
    error: null,
    booting: false,
    ...over,
  };
  render(<ProjectList {...props} />);
  return props;
}

describe("ProjectList", () => {
  it("renders_one_row_per_project_with_version_and_update_time", () => {
    renderList();

    // 每行：标题是主文本，版本号用等宽，时间跟在后面
    expect(screen.getByRole("button", { name: /订单中台/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /登录改版/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /数据看板/ })).toBeTruthy();
    // 版本号与日期都渲染在行内（等宽 meta，与左栏章节列表同一套）
    expect(screen.getAllByText(/^v\d+$/)).toHaveLength(THREE.length);
    expect(screen.getAllByText("2026-10-09").length).toBeGreaterThan(0);

    for (const item of THREE) {
      expect(screen.getByText(`v${item.version_no}`)).toBeTruthy();
    }
    // 时间取日期部分（`ProjectSummaryView.updated_at` 是 ISO 串）
    expect(screen.getAllByText(/^2026-10-/).length).toBe(THREE.length);
  });

  it("clicking_a_row_opens_that_project", () => {
    const props = renderList();

    screen.getByRole("button", { name: /登录改版/ }).click();

    expect(props.onOpen).toHaveBeenCalledWith("p2");
  });

  it("no_projects_shows_the_onboarding_form_instead_of_an_empty_list", () => {
    renderList({ projects: [] });

    // 空态只有一份：直接把 Onboarding 摆上来，不再造一个"还没有项目"
    expect(screen.getByRole("heading", { name: "先建一个项目" })).toBeTruthy();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("new_project_form_ignores_blank_and_trims_the_title", () => {
    const props = renderList();

    const input = screen.getByLabelText("项目名（一般就是产品名）");
    const submit = screen.getByRole("button", { name: "建项目" });

    // 空白不提交（按钮本身也禁用——两层，与 Onboarding 同口径）
    fireEvent.change(input, { target: { value: "   " } });
    submit.click();
    expect(props.onCreate).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "  订单中台  " } });
    submit.click();
    expect(props.onCreate).toHaveBeenCalledTimes(1);
    expect(props.onCreate).toHaveBeenCalledWith("订单中台");
  });

  it("loading_shows_a_skeleton_rather_than_an_empty_list", () => {
    renderList({ projects: [], booting: true });

    expect(screen.getByLabelText("正在读项目列表")).toBeTruthy();
    expect(screen.queryByRole("heading", { name: "先建一个项目" })).toBeNull();
  });

  it("every_row_is_a_real_button_so_keyboard_reaches_it", () => {
    renderList();

    // 每行是一个 <button>（不是可点的 div）：Tab 能聚焦、Enter 能触发。
    // 这与 `06-前端.md` §7「全部交互元素是原生 button」同一条纪律。
    const rows = screen.getAllByRole("button", { name: /v\d/ });
    expect(rows).toHaveLength(THREE.length);
    for (const row of rows) {
      expect(row.tagName).toBe("BUTTON");
    }
  });
});
