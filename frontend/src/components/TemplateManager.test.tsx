// @vitest-environment jsdom
/* 模板管理两屏（`06` §2.3）。这几条钉的是**界面结构怎么表达约束**，不是文案：
 *  锚点章的删除按钮要 disabled、已占的 role 下拉要 disabled、删除走内联二次确认。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TemplateView } from "../api/types";
import { TemplateEditor } from "./TemplateEditor";
import { TemplateListView } from "./TemplateListView";

afterEach(cleanup);

const BUILTIN: TemplateView = {
  template_id: "standard",
  title: "标准版",
  builtin: true,
  sections: [
    { section_id: "s5", title: "功能需求", purpose: "p", role: "feature", slots: [] },
    { section_id: "s11", title: "风险", purpose: "p", role: "open_questions", slots: [] },
    { section_id: "s13", title: "版本历史", purpose: "p", role: "version_history", slots: [] },
  ],
};

const CUSTOM: TemplateView = {
  template_id: "tpl-1",
  title: "社区活动",
  builtin: false,
  sections: BUILTIN.sections,
};

describe("模板库：内置那份不给出编辑与删除", () => {
  it("内置那行只有「用这个建项目」", () => {
    render(
      <TemplateListView
        templates={[BUILTIN, CUSTOM]}
        onUse={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
        error={null}
      />,
    );

    const builtInRow = screen.getByText("标准版").closest("li")!;
    expect(builtInRow.textContent).toContain("内置");
    expect(builtInRow.textContent).not.toContain("编辑");
    expect(builtInRow.textContent).not.toContain("删除");
  });

  it("自建那份有编辑与删除", () => {
    render(
      <TemplateListView
        templates={[BUILTIN, CUSTOM]}
        onUse={vi.fn()}
        onEdit={vi.fn()}
        onDelete={vi.fn()}
        onNew={vi.fn()}
        error={null}
      />,
    );
    expect(screen.getByText("社区活动").closest("li")!.textContent).toContain("我的");
  });
});

describe("删除：内联二次确认，第一次点不发请求", () => {
  it("点一次变「确认删除？」，再点才调 onDelete", () => {
    const onDelete = vi.fn();
    render(
      <TemplateListView
        templates={[CUSTOM]}
        onUse={vi.fn()}
        onEdit={vi.fn()}
        onDelete={onDelete}
        onNew={vi.fn()}
        error={null}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "删除" }));
    expect(onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认删除？" }));
    expect(onDelete).toHaveBeenCalledWith("tpl-1");
  });
});

describe("编辑器：锚点章的删除按钮不可点", () => {
  it("三锚点章的「删」全 disabled，普通章不是", () => {
    render(
      <TemplateEditor
        title="社区活动"
        baseOptions={[CUSTOM]}
        sections={[
          ...BUILTIN.sections,
          { section_id: "s1", title: "背景", purpose: "p", role: "plain", slots: [] },
        ]}
        onChange={vi.fn()}
        onBack={vi.fn()}
        onSave={vi.fn()}
        error={null}
        pending={false}
      />,
    );

    const removeButtons = screen.getAllByRole("button", { name: "删掉这一章" });
    expect(removeButtons.filter((button) => button.hasAttribute("disabled"))).toHaveLength(3);
  });

  it("加一章：新 id 是最大值 +1", () => {
    const onChange = vi.fn();
    render(
      <TemplateEditor
        title="x"
        baseOptions={[CUSTOM]}
        sections={[
          ...BUILTIN.sections,
          { section_id: "s1", title: "背景", purpose: "p", role: "plain", slots: [] },
        ]}
        onChange={onChange}
        onBack={vi.fn()}
        onSave={vi.fn()}
        error={null}
        pending={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "加一章" }));
    expect(onChange).toHaveBeenCalled();
    const next = onChange.mock.calls.at(-1)![0].sections!;
    expect(next[next.length - 1].section_id).toBe("s14");
  });

  it("已占的锚点 role 在下拉里 disabled（不是等提交才报错）", () => {
    render(
      <TemplateEditor
        title="x"
        baseOptions={[CUSTOM]}
        sections={BUILTIN.sections}
        onChange={vi.fn()}
        onBack={vi.fn()}
        onSave={vi.fn()}
        error={null}
        pending={false}
      />,
    );

    const plain = screen.getAllByRole("combobox").at(-1)! as HTMLSelectElement;
    const feature = plain.querySelector("option[value='feature']")!;
    const steps = plain.querySelector("option[value='steps']")!;
    expect(feature.hasAttribute("disabled")).toBe(true);
    expect(steps.hasAttribute("disabled")).toBe(false);
  });

  it("锚点章不显示条数判据输入（后端本来也拒）", () => {
    render(
      <TemplateEditor
        title="x"
        baseOptions={[CUSTOM]}
        sections={[
          { section_id: "s5", title: "功能需求", purpose: "p", role: "feature", slots: [] },
          ...BUILTIN.sections.slice(1),
        ]}
        onChange={vi.fn()}
        onBack={vi.fn()}
        onSave={vi.fn()}
        error={null}
        pending={false}
      />,
    );
    // 只有非锚点章才问"至少几条"：三锚点齐了，一个都不该有
    expect(screen.queryAllByLabelText(/至少/)).toHaveLength(0);
  });

  it("非锚点章问「至少几条」且数字输入不得小于 1", () => {
    render(
      <TemplateEditor
        title="x"
        baseOptions={[CUSTOM]}
        sections={[
          ...BUILTIN.sections,
          { section_id: "s1", title: "背景", purpose: "p", role: "plain", slots: [] },
        ]}
        onChange={vi.fn()}
        onBack={vi.fn()}
        onSave={vi.fn()}
        error={null}
        pending={false}
      />,
    );

    const input = screen.getByLabelText(/至少/) as HTMLInputElement;
    expect(input.getAttribute("min")).toBe("1");
  });
});
describe("锚点不可取消：UI 不许制造后端必然拒绝的状态", () => {
  it("锚点章的角色下拉整个锁住（不能把唯一的 feature 改成别的）", () => {
    render(
      <TemplateEditor
        title="x"
        baseOptions={[CUSTOM]}
        sections={BUILTIN.sections}
        onChange={vi.fn()}
        onBack={vi.fn()}
        onSave={vi.fn()}
        error={null}
        pending={false}
      />,
    );

    // 每章一个角色下拉：三个全是锚点，所以三个都该锁
    const selects = screen.getAllByRole("combobox").filter((el) =>
      (el as HTMLSelectElement).getAttribute("aria-label")?.includes("角色"),
    );
    expect(selects).toHaveLength(3);
    for (const select of selects) {
      expect(select.hasAttribute("disabled")).toBe(true);
    }
  });

  it("普通章的角色下拉不锁", () => {
    render(
      <TemplateEditor
        title="x"
        baseOptions={[CUSTOM]}
        sections={[
          ...BUILTIN.sections,
          { section_id: "s1", title: "背景", purpose: "p", role: "plain", slots: [] },
        ]}
        onChange={vi.fn()}
        onBack={vi.fn()}
        onSave={vi.fn()}
        error={null}
        pending={false}
      />,
    );

    const plain = screen
      .getAllByRole("combobox")
      .filter((el) => el.getAttribute("aria-label") === "s1 角色")[0]!;
    expect(plain.hasAttribute("disabled")).toBe(false);
  });

  it("当前角色自己不标「（已占用）」（它就是占着它的那一条）", () => {
    render(
      <TemplateEditor
        title="x"
        baseOptions={[CUSTOM]}
        sections={[
          ...BUILTIN.sections,
          { section_id: "s1", title: "背景", purpose: "p", role: "plain", slots: [] },
        ]}
        onChange={vi.fn()}
        onBack={vi.fn()}
        onSave={vi.fn()}
        error={null}
        pending={false}
      />,
    );

    // s1 是 plain：plain 不标已占用；feature 已被 s5 占着 → 标
    const select = screen
      .getAllByRole("combobox")
      .filter((el) => el.getAttribute("aria-label") === "s1 角色")[0]!;
    const texts = [...select.querySelectorAll("option")].map((o) => o.textContent ?? "");
    expect(texts.find((t) => t.startsWith("普通内容章"))).toBe("普通内容章");
    expect(texts.find((t) => t.startsWith("功能需求"))).toBe("功能需求（已占用）");
  });
});
