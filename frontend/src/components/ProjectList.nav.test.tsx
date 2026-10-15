// @vitest-environment jsdom
/* §1 导航修复：左导航只保留两个真锚点，加 scrollspy 高亮；「模板库」改成按钮，
   走 onManageTemplates（与 TopBar 模板按钮同一动作）。 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import type { LimitsView, ProjectSummaryView, TemplateView } from "../api/types";
import { ProjectList } from "./ProjectList";

afterEach(cleanup);

// jsdom 没有 IntersectionObserver，先 mock（3 行，一次性）：
class FakeObserver {
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
