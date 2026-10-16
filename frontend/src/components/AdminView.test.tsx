// @vitest-environment jsdom
/* 管理后台（`06` §2.4）：概览 + 按状态分区 + 每行动作。
   fetch 打桩与断言体例照抄 `LoginView.test.tsx`（本仓无 jest-dom，用 textContent/属性）。

   组件只打 `/auth/stats` 一个端点——**需求变更（2026-10-16）**：原先打
   `/auth/users` + `/auth/usage` 两个，再在前端 join；那两个窄口径的读已经不够用，
   而在前端 join 两份响应就是两个真相源（动作之后两边时点还可能对不上）。 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { AuthProvider } from "../state/auth";
import { AdminView } from "./AdminView";

type FetchStub = Partial<Response>;

function jsonResponse(body: unknown, status = 200): FetchStub {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "",
    json: async () => body,
    text: async () => JSON.stringify(body),
  };
}

/** 记下每一次 fetch 的 url/init，并按路径后缀给回包。 */
function captureFetch(routes: Record<string, FetchStub>): { url: string; init: RequestInit }[] {
  const calls: { url: string; init: RequestInit }[] = [];
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    const path = String(url);
    calls.push({ url: path, init: init ?? {} });
    const key = Object.keys(routes).find((suffix) => path.endsWith(suffix));
    return (key === undefined ? jsonResponse({}, 200) : routes[key]) as Response;
  }) as unknown as typeof fetch;
  return calls;
}

const originalFetch = globalThis.fetch;

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  window.localStorage.clear();
});

function draw() {
  render(
    <AuthProvider>
      <AdminView />
    </AuthProvider>,
  );
}

const ADMIN_ME: FetchStub = jsonResponse({
  user_id: "u-root",
  username: "root",
  role: "admin",
  status: "active",
});

function stat(over: Record<string, unknown> = {}) {
  return {
    user_id: "u-x",
    username: "alice",
    role: "user",
    status: "active",
    projects: 0,
    rounds: 0,
    calls: 0,
    failed: 0,
    avg_duration_ms: null,
    total_tokens: 0,
    active_sessions: 0,
    last_seen_at: null,
    created_at: "2026-10-16T00:00:00+00:00",
    approved_at: null,
    approved_by: null,
    ...over,
  };
}

const TWO_USERS = jsonResponse({
  stats: [
    stat({
      user_id: "u-bob",
      username: "bob",
      status: "pending",
      created_at: "2026-10-16T12:00:00+00:00",
    }),
    stat({
      user_id: "u-root",
      username: "root",
      role: "admin",
      calls: 183,
      failed: 16,
      avg_duration_ms: 66726,
      total_tokens: 1493085,
      active_sessions: 3,
      last_seen_at: new Date().toISOString(),
      projects: 5,
      rounds: 19,
    }),
  ],
});

it("只打 /auth/stats 一个端点（不再前端 join users + usage）", async () => {
  const calls = captureFetch({ "/auth/me": ADMIN_ME, "/auth/stats": TWO_USERS });
  draw();
  await screen.findByText("bob");

  const statsCalls = calls.filter((call) => call.url.endsWith("/auth/stats"));
  expect(statsCalls.length).toBeGreaterThan(0);
  expect(calls.some((call) => call.url.endsWith("/auth/usage"))).toBe(false);
  expect(calls.some((call) => call.url.endsWith("/auth/users"))).toBe(false);
});

it("按状态分区：待审批与已生效各一栏，异常栏空着不画", async () => {
  captureFetch({ "/auth/me": ADMIN_ME, "/auth/stats": TWO_USERS });
  draw();
  await screen.findByText("bob");

  expect(screen.getByLabelText("待审批")).toBeTruthy();
  expect(screen.getByLabelText("已生效")).toBeTruthy();
  // 这两个是异常栏，库里没人 → 不占版面
  expect(screen.queryByLabelText("封禁中")).toBeNull();
  expect(screen.queryByLabelText("已拒绝")).toBeNull();
});

it("admin 行没有操作按钮——不给死按钮", async () => {
  captureFetch({ "/auth/me": ADMIN_ME, "/auth/stats": TWO_USERS });
  draw();
  await screen.findByText("root");

  const row = screen.getByText("root").closest("tr");
  expect(row).toBeTruthy();
  expect(row!.querySelectorAll("button").length).toBe(0);
});

it("pending 行给批准与拒绝，active 行给封禁", async () => {
  captureFetch({ "/auth/me": ADMIN_ME, "/auth/stats": TWO_USERS });
  draw();
  await screen.findByText("bob");

  // 只有 bob（pending）有批准/拒绝；root（admin）没有；库里没有 active 普通用户
  expect(screen.getAllByRole("button", { name: "批准" }).length).toBe(1);
  expect(screen.getAllByRole("button", { name: "拒绝" }).length).toBe(1);
  expect(screen.queryByRole("button", { name: "封禁" })).toBeNull();
});

it("批准打到 POST /api/auth/approve，带 user_id", async () => {
  const calls = captureFetch({
    "/auth/me": ADMIN_ME,
    "/auth/stats": TWO_USERS,
    "/auth/approve": jsonResponse(stat({ user_id: "u-bob", status: "active" })),
  });
  draw();
  await screen.findByText("bob");

  fireEvent.click(screen.getByRole("button", { name: "批准" }));

  await waitFor(() => expect(calls.some((c) => c.url.endsWith("/auth/approve"))).toBe(true));
  const call = calls.find((c) => c.url.endsWith("/auth/approve"))!;
  expect(call.init.method).toBe("POST");
  expect(JSON.parse(String(call.init.body))).toEqual({ user_id: "u-bob" });
});

it("拒绝打到 POST /api/auth/reject，带 user_id", async () => {
  const calls = captureFetch({
    "/auth/me": ADMIN_ME,
    "/auth/stats": TWO_USERS,
    "/auth/reject": jsonResponse(stat({ user_id: "u-bob", status: "rejected" })),
  });
  draw();
  await screen.findByText("bob");

  fireEvent.click(screen.getByRole("button", { name: "拒绝" }));

  await waitFor(() => expect(calls.some((c) => c.url.endsWith("/auth/reject"))).toBe(true));
  expect(JSON.parse(String(calls.find((c) => c.url.endsWith("/auth/reject"))!.init.body))).toEqual({
    user_id: "u-bob",
  });
});

it("失败数与平均耗时出现在行里（调用数好看但失败被藏起来是这一屏最大的坑）", async () => {
  captureFetch({ "/auth/me": ADMIN_ME, "/auth/stats": TWO_USERS });
  draw();
  await screen.findByText("root");

  const row = screen.getByText("root").closest("tr")!;
  expect(row.textContent).toContain("183");
  expect(row.textContent).toContain("失败 16");
  expect(row.textContent).toContain("1m07s");
});

it("概览条把总账算出来：失败率与 token 缩写", async () => {
  captureFetch({ "/auth/me": ADMIN_ME, "/auth/stats": TWO_USERS });
  draw();
  await screen.findByText("bob");

  const facts = screen.getByLabelText("总览");
  expect(facts.textContent).toContain("2"); // 账号
  expect(facts.textContent).toContain("1"); // 待审批
  expect(facts.textContent).toContain("183");
  expect(facts.textContent).toContain("16");
  expect(facts.textContent).toContain("1.49M");
});

it("从没登录过的人明说「从未登录」", async () => {
  captureFetch({ "/auth/me": ADMIN_ME, "/auth/stats": TWO_USERS });
  draw();
  await screen.findByText("bob");

  const row = screen.getByText("bob").closest("tr")!;
  expect(row.textContent).toContain("从未登录");
});

it("非 admin 渲染无权限，一个管理端点都不打", async () => {
  const calls = captureFetch({
    "/auth/me": jsonResponse({ user_id: "u-b", username: "bob", role: "user", status: "active" }),
  });
  draw();

  await screen.findByText(/无权限/);
  expect(calls.some((c) => c.url.endsWith("/auth/stats"))).toBe(false);
});