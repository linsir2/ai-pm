// @vitest-environment jsdom
/* 管理后台（Task 8）：审批队列 / 用户列表 / 封禁 / 用量。
   fetch 打桩与断言体例照抄 `LoginView.test.tsx`（本仓无 jest-dom，用 textContent/属性）。 */

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

/** 恢复会话时 `/auth/me` 回的 admin 身份（新用例复用，不改既有断言）。 */
const ADMIN_ME: FetchStub = jsonResponse({
  user_id: "u-root",
  username: "root",
  role: "admin",
  status: "active",
});

it("审批通过按钮打到 POST /api/auth/approve，带 user_id", async () => {
  const calls = captureFetch({
    "/auth/me": jsonResponse({
      user_id: "u-root",
      username: "root",
      role: "admin",
      status: "active",
    }),
    "/auth/users": jsonResponse({
      users: [
        { user_id: "u-root", username: "root", role: "admin", status: "active" },
        { user_id: "u-alice", username: "alice", role: "user", status: "pending" },
      ],
    }),
    "/auth/usage": jsonResponse({
      usage: [
        { user_id: "u-root", username: "root", calls: 0 },
        { user_id: "u-alice", username: "alice", calls: 3 },
      ],
    }),
    "/auth/approve": jsonResponse(
      { user_id: "u-alice", username: "alice", role: "user", status: "active" },
      200,
    ),
  });
  draw();

  fireEvent.click(await screen.findByRole("button", { name: "批准" }));

  await waitFor(() =>
    expect(calls.filter((call) => call.url.endsWith("/auth/approve"))).toHaveLength(1),
  );
  const approve = calls.find((call) => call.url.endsWith("/auth/approve"))!;
  expect(approve.init.method).toBe("POST");
  expect(JSON.parse(String(approve.init.body))).toEqual({ user_id: "u-alice" });
});

it("非 admin 渲染无权限，且不碰管理端点", async () => {
  const calls = captureFetch({
    "/auth/me": jsonResponse({
      user_id: "u-bob",
      username: "bob",
      role: "user",
      status: "active",
    }),
  });
  draw();

  await waitFor(() => expect(screen.getByText(/无权限/)).toBeTruthy());
  expect(calls.some((call) => call.url.endsWith("/auth/users"))).toBe(false);
  expect(calls.some((call) => call.url.endsWith("/auth/usage"))).toBe(false);
});

it("拒绝按钮打到 POST /api/auth/reject，带 user_id", async () => {
  const calls = captureFetch({
    "/auth/me": ADMIN_ME,
    "/auth/users": jsonResponse({
      users: [
        { user_id: "u-alice", username: "alice", role: "user", status: "pending" },
      ],
    }),
    "/auth/usage": jsonResponse({ usage: [] }),
    "/auth/reject": jsonResponse({
      user_id: "u-alice",
      username: "alice",
      role: "user",
      status: "rejected",
    }),
  });
  draw();

  fireEvent.click(await screen.findByRole("button", { name: "拒绝" }));

  await waitFor(() =>
    expect(calls.filter((call) => call.url.endsWith("/auth/reject"))).toHaveLength(1),
  );
  const reject = calls.find((call) => call.url.endsWith("/auth/reject"))!;
  expect(reject.init.method).toBe("POST");
  expect(JSON.parse(String(reject.init.body))).toEqual({ user_id: "u-alice" });
});

it("封禁按钮打到 POST /api/auth/ban，带 user_id", async () => {
  const calls = captureFetch({
    "/auth/me": ADMIN_ME,
    "/auth/users": jsonResponse({
      users: [
        { user_id: "u-alice", username: "alice", role: "user", status: "active" },
      ],
    }),
    "/auth/usage": jsonResponse({ usage: [] }),
    "/auth/ban": jsonResponse({
      user_id: "u-alice",
      username: "alice",
      role: "user",
      status: "banned",
    }),
  });
  draw();

  fireEvent.click(await screen.findByRole("button", { name: "封禁" }));

  await waitFor(() =>
    expect(calls.filter((call) => call.url.endsWith("/auth/ban"))).toHaveLength(1),
  );
  const ban = calls.find((call) => call.url.endsWith("/auth/ban"))!;
  expect(ban.init.method).toBe("POST");
  expect(JSON.parse(String(ban.init.body))).toEqual({ user_id: "u-alice" });
});

it("banned 用户行显示「解封」，点了复用 POST /api/auth/approve", async () => {
  const calls = captureFetch({
    "/auth/me": ADMIN_ME,
    "/auth/users": jsonResponse({
      users: [
        { user_id: "u-alice", username: "alice", role: "user", status: "banned" },
      ],
    }),
    "/auth/usage": jsonResponse({ usage: [] }),
    "/auth/approve": jsonResponse({
      user_id: "u-alice",
      username: "alice",
      role: "user",
      status: "active",
    }),
  });
  draw();

  fireEvent.click(await screen.findByRole("button", { name: "解封" }));

  await waitFor(() =>
    expect(calls.filter((call) => call.url.endsWith("/auth/approve"))).toHaveLength(1),
  );
  const approve = calls.find((call) => call.url.endsWith("/auth/approve"))!;
  expect(approve.init.method).toBe("POST");
  expect(JSON.parse(String(approve.init.body))).toEqual({ user_id: "u-alice" });
});

it("用户列表按 api.usage 渲染用量（缺省 0 由纯函数给）", async () => {
  captureFetch({
    "/auth/me": ADMIN_ME,
    "/auth/users": jsonResponse({
      users: [
        { user_id: "u-alice", username: "alice", role: "user", status: "active" },
        { user_id: "u-bob", username: "bob", role: "user", status: "active" },
        { user_id: "u-carol", username: "carol", role: "user", status: "active" },
      ],
    }),
    "/auth/usage": jsonResponse({
      usage: [
        { user_id: "u-alice", username: "alice", calls: 3 },
        { user_id: "u-bob", username: "bob", calls: 5 },
      ],
    }),
  });
  draw();

  await screen.findByText("alice");
  expect(screen.getByText(/用量 3/).textContent).toContain("用量 3");
  expect(screen.getByText(/用量 5/).textContent).toContain("用量 5");
  // carol 不在 usage 列表里 → 纯函数补 0
  expect(screen.getByText(/用量 0/).textContent).toContain("用量 0");
});
