// @vitest-environment jsdom
/* §1 导航修复：左导航只保留两个真锚点，加 scrollspy 高亮；「模板库」改成按钮，
   走 onManageTemplates（与 TopBar 模板按钮同一动作）。 */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { LimitsView, ProjectSummaryView, TemplateView } from "../api/types";
import { ProjectList } from "./ProjectList";

let observerCallback: IntersectionObserverCallback | null = null;

afterEach(() => {
  cleanup();
  observerCallback = null;
});

// jsdom 没有 IntersectionObserver，先 mock（回调捕获版，供 scrollspy 触发）：
class FakeObserver {
  constructor(callback: IntersectionObserverCallback) {
    observerCallback = callback;
  }
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal("IntersectionObserver", FakeObserver);

const LIMITS: LimitsView = {
  max_seat_rounds: 3,
  max_questionnaires: 2,
  max_open_items_per_batch: 3,
  max_sim_review_personas: 3,
  max_sim_review_features: 2,
  max_discussion_turns: 12,
};

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
];

const proj: ProjectSummaryView = {
  project_id: "p1",
  title: "订单中台",
  version_no: 3,
  updated_at: "2026-10-09T14:23:00+08:00",
  template_id: "standard",
  template_title: "标准版",
};

function intersect(id: string) {
  const target = document.getElementById(id);
  if (observerCallback === null || target === null) throw new Error("scrollspy observer 未就绪");
  const entry = { isIntersecting: true, target } as unknown as IntersectionObserverEntry;
  act(() => {
    observerCallback?.([entry], {} as IntersectionObserver);
  });
}

it("左导航只有两个真锚点，模板库是按钮", () => {
  const fn = vi.fn();
  render(
    <ProjectList
      projects={[proj]}
      onOpen={vi.fn()}
      onCreate={vi.fn()}
      pending={false}
      error={null}
      booting={false}
      limits={LIMITS}
      templates={TEMPLATES}
      onManageTemplates={fn}
    />,
  );

  expect(screen.getByRole("link", { name: "新建项目" }).getAttribute("href")).toBe(
    "#home-new",
  );
  expect(screen.getByRole("link", { name: "最近项目" }).getAttribute("href")).toBe(
    "#home-recent",
  );
  fireEvent.click(screen.getByRole("button", { name: "模板库" }));
  expect(fn).toHaveBeenCalledTimes(1);
});

it("左导航当前段标 aria-current=location（页内锚点不用 TopBar 的 page）", () => {
  render(
    <ProjectList
      projects={[proj]}
      onOpen={vi.fn()}
      onCreate={vi.fn()}
      pending={false}
      error={null}
      booting={false}
      limits={LIMITS}
      templates={TEMPLATES}
      onManageTemplates={vi.fn()}
    />,
  );

  expect(screen.getByRole("link", { name: "新建项目" }).getAttribute("aria-current")).toBe(
    "location",
  );
  expect(screen.getByRole("link", { name: "最近项目" }).getAttribute("aria-current")).toBeNull();

  intersect("home-recent");

  expect(screen.getByRole("link", { name: "最近项目" }).getAttribute("aria-current")).toBe(
    "location",
  );
  expect(screen.getByRole("link", { name: "新建项目" }).getAttribute("aria-current")).toBeNull();
});

it("模板库是跨视图按钮：从不标 aria-current（page 是 TopBar 那边的语义）", () => {
  render(
    <ProjectList
      projects={[proj]}
      onOpen={vi.fn()}
      onCreate={vi.fn()}
      pending={false}
      error={null}
      booting={false}
      limits={LIMITS}
      templates={TEMPLATES}
      onManageTemplates={vi.fn()}
    />,
  );

  expect(screen.getByRole("button", { name: "模板库" }).getAttribute("aria-current")).toBeNull();
});

it("scrollspy：home-recent 相交后对应导航项带 active 类、另一项不带", () => {
  render(
    <ProjectList
      projects={[proj]}
      onOpen={vi.fn()}
      onCreate={vi.fn()}
      pending={false}
      error={null}
      booting={false}
      limits={LIMITS}
      templates={TEMPLATES}
      onManageTemplates={vi.fn()}
    />,
  );

  intersect("home-recent");

  expect(screen.getByRole("link", { name: "最近项目" }).getAttribute("class")).toContain(
    "home__navlink--active",
  );
  expect(screen.getByRole("link", { name: "新建项目" }).getAttribute("class")).not.toContain(
    "home__navlink--active",
  );
});
