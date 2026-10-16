// @vitest-environment jsdom
/* 项目列表页：左导航壳 + 右内容（上画廊下密行）。行带模板名（后端摘要给的，不编）。

   这一屏钉的事（旧五件 + 新三件）：
   ① 每个项目一行，行上带版本号/更新时间/**模板名**；
   ② 点行进工作区（`onOpen` 收到的是那一行的 id）；
   ③ **一个项目都没有时不画空列表**，直接复用 `Onboarding`（空态只有一份）；
   ④ 新建表单的形状（空白禁提交/去空白/选模板）由 `NewProjectForm.test.tsx` 钉，
      这里只钉"表单的提交真的递到 `onCreate(title, templateId)`"；
   ⑤ 加载中显示骨架，不显示空列表；
   ⑥ 左导航三项都在（多用户以后只加项）；
   ⑦ 不再断言"一份 13 章"（模板时代它是错的）；
   ⑧ 每行仍是原生 button（键盘纪律），仍有 glyph 与 ›。
   作废（设计改了）：四格元信息条及其三个用例（压成一行小字）、模型截断格
   （小字行不截断）、旧 `CreateProject` 内联表单（抽成 `NewProjectForm`）。 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { LimitsView, ProjectSummaryView, TemplateView } from "../api/types";
import { ProjectList } from "./ProjectList";

afterEach(cleanup);

const LIMITS: LimitsView = {
  max_seat_rounds: 3,
  max_questionnaires: 2,
  max_open_items_per_batch: 3,
  max_sim_review_personas: 3,
  max_sim_review_features: 2,
  max_discussion_turns: 12,
};

function project(
  id: string,
  title: string,
  version_no: number,
  updated_at: string,
  template_id = "standard",
  template_title = "标准版",
): ProjectSummaryView {
  return {
    project_id: id,
    title,
    version_no,
    updated_at,
    template_id,
    template_title,
  };
}

const THREE = [
  project("p1", "订单中台", 3, "2026-10-09T14:23:00+08:00", "light", "轻量版"),
  project("p2", "登录改版", 0, "2026-10-08T09:12:00+08:00"),
  project("p3", "数据看板", 1, "2026-10-01T20:05:00+08:00"),
];

const TEMPLATES: TemplateView[] = [
  {
    template_id: "standard",
    title: "标准版",
    sections: Array.from({ length: 13 }, (_, index) => ({
      section_id: `s${index + 1}`,
      title: `章${index + 1}`,
      purpose: "摆设",
      role: "plain",
      slots: [],
    })),
  },
  {
    template_id: "light",
    title: "轻量版",
    sections: Array.from({ length: 6 }, (_, index) => ({
      section_id: `s${index + 1}`,
      title: `章${index + 1}`,
      purpose: "摆设",
      role: "plain",
      slots: [],
    })),
  },
];

function renderList(over: Partial<Parameters<typeof ProjectList>[0]> = {}) {
  const props = {
    projects: THREE,
    onOpen: vi.fn(),
    onCreate: vi.fn(),
    pending: false,
    error: null,
    booting: false,
    limits: LIMITS,
    templates: TEMPLATES,
    onManageTemplates: vi.fn(),
    ...over,
  };
  render(<ProjectList {...props} />);
  return props;
}

describe("ProjectList", () => {
  it("renders_one_row_per_project_with_version_time_and_template", () => {
    renderList();

    // 每行：标题是主文本，版本号用等宽，时间跟在后面，模板名是后端给的
    expect(screen.getByRole("button", { name: /订单中台/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /登录改版/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /数据看板/ })).toBeTruthy();
    expect(screen.getAllByText(/^v\d+$/)).toHaveLength(THREE.length);
    expect(screen.getAllByText("2026-10-09").length).toBeGreaterThan(0);

    for (const item of THREE) {
      expect(screen.getByText(`v${item.version_no}`)).toBeTruthy();
    }
    // 时间取日期部分。只在列表里数——页眉小字里也有一个"最近更新"的日期，那是另一处。
    const rows = screen.getByRole("list").querySelectorAll(".projects__row");
    expect(rows).toHaveLength(THREE.length);
    for (const row of rows) {
      expect(row.textContent).toMatch(/v\d2026-10-\d\d/);
    }
    // 模板名在行上（p1 是轻量版，其余标准版）
    const recent = within(screen.getByRole("list"));
    expect(recent.getByText("轻量版")).toBeTruthy();
    expect(recent.getAllByText("标准版")).toHaveLength(2);
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

  it("creating_from_the_list_passes_title_and_picked_template", () => {
    const props = renderList();

    // "轻量版"在画廊和 p1 行里各出现一次：点选要点画廊里那张
    fireEvent.click(within(screen.getByRole("radiogroup")).getByText("轻量版"));
    fireEvent.change(screen.getByLabelText("项目名（一般就是产品名）"), {
      target: { value: "新产品" },
    });
    screen.getByRole("button", { name: "建项目" }).click();

    expect(props.onCreate).toHaveBeenCalledWith("新产品", "light");
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
    const rows = screen.getByRole("list").querySelectorAll("button.projects__row");
    expect(rows).toHaveLength(THREE.length);
    for (const row of rows) {
      expect(row.tagName).toBe("BUTTON");
    }
  });

  it("each_row_carries_a_glyph_of_the_first_character_of_the_title", () => {
    renderList();

    // 每行一个身份方块（衬线首字母）：扫读时的落点，与顶栏 logo 同一种"图标在方形里"的语言
    expect(screen.getByText("订")).toBeTruthy();
    expect(screen.getByText("登")).toBeTruthy();
    expect(screen.getByText("数")).toBeTruthy();
  });

  it("the_row_affords_entering_with_a_chevron", () => {
    renderList();

    // 行尾一个 ›：点进去的暗示。原来完全没有，只看不出可点。
    const rows = screen.getByRole("list").querySelectorAll("button.projects__row");
    for (const row of rows) {
      expect(row.textContent).toContain("›");
    }
  });

  it("left_nav_has_the_three_anchors", () => {
    const props = renderList({ onManageTemplates: vi.fn() });

    expect(screen.getByRole("link", { name: "新建项目" })).toBeTruthy();
    expect(screen.getByRole("link", { name: "最近项目" })).toBeTruthy();
    const templatesButton = screen.getByRole("button", { name: "模板库" });
    fireEvent.click(templatesButton);
    expect(props.onManageTemplates).toHaveBeenCalledTimes(1);
  });

  it("manage_templates_entry_calls_back_once", () => {
    const props = renderList({ onManageTemplates: vi.fn() });
    fireEvent.click(screen.getByRole("button", { name: "管理模板 →" }));
    expect(props.onManageTemplates).toHaveBeenCalledTimes(1);
  });

  // **需求变更（2026-10-16 文档库重做）**：页头原本把模型名与席位轮上限也摆出来，
  // 本次**删掉**——那是运行信息（属于设置与调试），写在"你的项目"页头会让人以为
  // 该在这里换模型。断言跟着改，不是为了让测试变绿。
  it("meta_行只摆项目数与最近更新，不再摆模型名与席位轮上限", () => {
    renderList({});  // `model` prop 已随本次改动一并移除（页头不再显示运行信息）

    expect(screen.getByText(/3 个项目/)).toBeTruthy();
    expect(screen.queryByText(/openai\/qwen3\.8-flash/)).toBeNull();
    expect(screen.queryByText(/席位轮上限/)).toBeNull();
    // "一份 13 章"这种断言在模板时代就是错的，仍然不许出现
    expect(screen.queryByText(/一份 13 章/)).toBeNull();
  });
});
