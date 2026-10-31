// @vitest-environment jsdom
// 经真实 App/client 走导航；只替换外部 HTTP 与 SSE。
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import App from "./App";
import type { ProjectSummaryView, WorkspaceView } from "./api/types";

const projects: ProjectSummaryView[] = [
  { project_id: "p1", title: "订单中台", version_no: 3, updated_at: "2026-10-31T10:00:00Z", template_id: "standard", template_title: "标准版" },
  { project_id: "p2", title: "编程助手", version_no: 2, updated_at: "2026-10-30T10:00:00Z", template_id: "light", template_title: "轻量版" },
];
const user = { user_id: "u1", username: "admin", role: "admin", status: "active" };
let signedIn: boolean;
let reads: string[];

function workspace(project: ProjectSummaryView): WorkspaceView {
  return {
    project,
    document: {
      document_id: `doc-${project.project_id}`, project_id: project.project_id,
      title: project.title, version_no: project.version_no, preference_entries: [],
      sections: [{ section_id: "s1", title: "背景与问题", role: "context", blocks: [{
        block_id: "s1-text", kind: "text", feature_slug: null, field: null, field_title: null,
        multiple: true, manually_edited: false, children: [],
        entries: [{ entry_id: `${project.project_id}-entry`, text: `${project.title}已确认正文`, refs: [] }],
      }] }],
    },
    round: null, readiness: [], memory: [],
  };
}

beforeEach(() => {
  signedIn = true;
  reads = [];
  window.history.replaceState(null, "", "/");
  window.localStorage.clear();
  vi.stubGlobal("EventSource", class extends EventTarget { close() {} });
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
  vi.stubGlobal("fetch", async (url: unknown) => {
    const path = String(url);
    reads.push(path);
    if (path.endsWith("/auth/me") && !signedIn) {
      return new Response(JSON.stringify({ code: "unauthorized", message: "请登录" }), { status: 401 });
    }
    if (path.endsWith("/auth/login")) signedIn = true;
    if (path.endsWith("/auth/logout")) signedIn = false;
    const project = projects.find((item) => path === `/api/projects/${item.project_id}/workspace`);
    const value = path.endsWith("/auth/me") || path.endsWith("/auth/login") ? user
      : path === "/api/projects" ? projects
      : project !== undefined ? workspace(project)
      : path.endsWith("/health") ? { model: "test" }
      : path.endsWith("/schema") ? null
      : path.endsWith("/auth/stats") ? { stats: [] }
      : [];
    return new Response(JSON.stringify(value));
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

it("普通进入先到文档中心，不自动打开列表中的第一个项目", async () => {
  render(<App />);
  await screen.findByRole("button", { name: /订单中台/ });
  expect(screen.getByRole("main", { name: "文档中心" })).toBeTruthy();
  expect(reads.some((path) => path.endsWith("/workspace"))).toBe(false);
});

it("重新登录后到文档中心，即使登录前 URL 留着项目与页内锚点", async () => {
  signedIn = false;
  window.history.replaceState(null, "", "/?project=p2#home-recent");
  render(<App />);
  fireEvent.change(await screen.findByLabelText("用户名"), { target: { value: "admin" } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: "password" } });
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await screen.findByRole("button", { name: /编程助手/ });
  expect(screen.getByRole("main", { name: "文档中心" })).toBeTruthy();
  expect(window.location.search).toBe("");
  expect(window.location.hash).toBe("");
});

it("已登录时刷新显式项目链接，恢复该项目而不是第一个项目", async () => {
  window.history.replaceState(null, "", "/?project=p2");
  render(<App />);
  await screen.findByText("编程助手已确认正文");
  expect(screen.queryByText("订单中台已确认正文")).toBeNull();
  expect(window.location.search).toBe("?project=p2");
});

it("点项目才进正文，返回文档中心后刷新仍是文档中心", async () => {
  const app = render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: /编程助手/ }));
  await screen.findByText("编程助手已确认正文");
  expect(window.location.search).toBe("?project=p2");
  fireEvent.click(screen.getByRole("button", { name: "文档中心" }));
  await screen.findByRole("main", { name: "文档中心" });
  expect(window.location.search).toBe("");
  app.unmount();
  render(<App />);
  await screen.findByRole("button", { name: /订单中台/ });
  expect(screen.queryByText("编程助手已确认正文")).toBeNull();
});

it("管理后台有明确的返回按钮，返回后文档中心导航标出当前位置", async () => {
  window.history.replaceState(null, "", "/?project=p1");
  render(<App />);
  await screen.findByText("订单中台已确认正文");
  fireEvent.click(screen.getByRole("button", { name: "管理后台" }));
  await screen.findByRole("heading", { name: "管理后台" });
  fireEvent.click(screen.getByRole("button", { name: /返回文档中心/ }));
  await screen.findByRole("main", { name: "文档中心" });
  expect(screen.getByRole("button", { name: "文档中心" }).getAttribute("aria-current")).toBe("page");
});

it("模板库有明确的返回按钮，文档中心 Logo 不会反向打开隐藏项目", async () => {
  window.history.replaceState(null, "", "/?project=p1");
  render(<App />);
  await screen.findByText("订单中台已确认正文");
  fireEvent.click(screen.getByRole("button", { name: "PM Studio" }));
  await screen.findByRole("button", { name: /订单中台/ });
  fireEvent.click(screen.getByRole("button", { name: "模板" }));
  await screen.findByRole("main", { name: "模板库" });
  fireEvent.click(screen.getByRole("button", { name: /返回文档中心/ }));
  fireEvent.click(screen.getByRole("button", { name: "PM Studio" }));
  await waitFor(() => expect(screen.getByRole("main", { name: "文档中心" })).toBeTruthy());
  expect(screen.queryByText("订单中台已确认正文")).toBeNull();
});
