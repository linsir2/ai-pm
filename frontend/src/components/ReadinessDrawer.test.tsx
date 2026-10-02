// @vitest-environment jsdom
/* 就绪度清单（`P29` F6）：`06-前端.md` §2.2 承诺"点底栏那个'就差 N 项'打开，按章分组"，
   以前那句是死文本。这一份钉：分组、逐条、阻塞/提示分得开、"没有缺口"说实话。 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { DocumentView, ReadinessItemView } from "../api/types";
import { ReadinessDrawer } from "./ReadinessDrawer";

afterEach(cleanup);

const items: ReadinessItemView[] = [
  {
    section_id: "s1",
    code: "section_min_entries",
    title: "背景与问题还缺 1 条内容",
    detail: "至少 1 条：现状 / 问题 / 不做的代价",
    severity: "blocker",
  },
  {
    section_id: "s9",
    code: "section_kind_present",
    title: "技术方案还没写决策",
    detail: "用 decision 块写",
    severity: "warn",
  },
];

const doc = {
  sections: [
    { section_id: "s1", title: "背景与问题" },
    { section_id: "s9", title: "技术方案与约束" },
  ],
} as unknown as DocumentView;

describe("ReadinessDrawer", () => {
  it("按章分组，每条写清楚是阻塞还是提示", () => {
    render(<ReadinessDrawer items={items} document={doc} onCollapse={() => undefined} />);

    expect(screen.getByText(/就绪度 · 阻塞 1 项 \/ 提示 1 项/)).toBeTruthy();
    expect(screen.getByText(/s1 背景与问题/)).toBeTruthy();
    expect(screen.getByText(/s9 技术方案与约束/)).toBeTruthy();
    expect(screen.getByText("背景与问题还缺 1 条内容")).toBeTruthy();
    expect(screen.getByText("阻塞")).toBeTruthy();
    expect(screen.getByText("提示")).toBeTruthy();
  });

  it("没有缺口就明说就绪，不摆空壳", () => {
    render(<ReadinessDrawer items={[]} document={doc} onCollapse={() => undefined} />);

    expect(screen.getByText(/没有缺口/)).toBeTruthy();
  });
});
