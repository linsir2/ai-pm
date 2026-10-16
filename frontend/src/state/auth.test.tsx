// @vitest-environment jsdom
/* 认证会话与守卫（Task 7）：一个 AuthProvider 管身份，App 无 user 即渲染登录页。
   fetch 打桩体例照抄 `api/client.test.ts`；断言不用 jest-dom（本仓没有）。 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import App from "../App";
import { api } from "../api/client";
import { AuthProvider, useAuth } from "./auth";

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

/** 按路径查表的 fetch 替身：表里查不到就走 200 空回包。 */
function stubFetch(routes: Record<string, FetchStub>): { url: string; init: RequestInit }[] {
  const calls: { url: string; init: RequestInit }[] = [];
  globalThis.fetch = (async (url: unknown, init?: RequestInit) => {
    const path = String(url);
    calls.push({ url: path, init: init ?? {} });
    const key = Object.keys(routes).find((suffix) => path.endsWith(suffix));
    return (key === undefined ? jsonResponse({}, 200) : routes[key]) as Response;
  }) as unknown as typeof fetch;
  return calls;
}

/** 动态替身：每次调用都问一遍 handler，好在用例中途换回包。 */
function stubFetchDynamic(handler: (path: string) => FetchStub): void {
  globalThis.fetch = (async (url: unknown) =>
    handler(String(url)) as Response) as unknown as typeof fetch;
}

const UNAUTH_ME: FetchStub = jsonResponse(
  { code: "unauthenticated", message: "未登录" },
  401,
);
const AUTH_ME: FetchStub = jsonResponse(
  { user_id: "u1", username: "alice", role: "member", status: "active" },
  200,
);

const originalFetch = globalThis.fetch;

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  window.localStorage.clear();
});

it("未登录渲染登录页不渲染工作区", async () => {
  stubFetch({ "/auth/me": UNAUTH_ME });

  render(<App />);

  await waitFor(() => expect(screen.getByRole("button", { name: "登录" })).toBeTruthy());
  // 工作区里才有的东西一个都不该出现
  expect(screen.queryByRole("button", { name: "导出" })).toBeNull();
  expect(screen.queryByRole("button", { name: "模板" })).toBeNull();
  expect(window.localStorage.getItem("pm_session")).toBeNull();
});

it("401 跳登录", async () => {
  // 先以登录态起步，再让后续调用撞上 401。
  let me: FetchStub = AUTH_ME;
  stubFetchDynamic((path) => (path.endsWith("/auth/me") ? me : jsonResponse({}, 401)));

  function Probe() {
    const { user } = useAuth();
    if (user === null) return <div>login-screen</div>;
    return (
      <button
        type="button"
        onClick={() => {
          void api.listProjects().catch(() => undefined);
        }}
      >
        workspace:{user.username}
      </button>
    );
  }

  render(
    <AuthProvider>
      <Probe />
    </AuthProvider>,
  );

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /workspace:alice/ })).toBeTruthy(),
  );

  me = UNAUTH_ME;
  fireEvent.click(screen.getByRole("button", { name: /workspace:alice/ }));

  await waitFor(() => expect(screen.getByText("login-screen")).toBeTruthy());
});
