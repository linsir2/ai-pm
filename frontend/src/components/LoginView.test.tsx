// @vitest-environment jsdom
/* 登录 / 注册一屏两态（Task 7）。mock fetch 体例照抄 `api/client.test.ts`：
   经公开入口（AuthProvider 的 login/register → api 单例）打桩，不 mock 模块。 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { AuthProvider } from "../state/auth";
import { LoginView } from "./LoginView";

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

/** 记下每一次 fetch 的 url/init，并按路径给回包。 */
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

/** AuthProvider 一挂载就 GET /auth/me 恢复会话；未登录（401）返回。 */
const UNAUTH_ME: Record<string, FetchStub> = { "/auth/me": jsonResponse({}, 401) };

function draw() {
  render(
    <AuthProvider>
      <LoginView />
    </AuthProvider>,
  );
}

async function fill(username: string, password: string) {
  fireEvent.change(await screen.findByLabelText("用户名"), { target: { value: username } });
  fireEvent.change(screen.getByLabelText("密码"), { target: { value: password } });
}

it("登录提交打到 /api/auth/login，带上用户名与密码", async () => {
  const calls = captureFetch({
    ...UNAUTH_ME,
    "/auth/login": jsonResponse(
      { user_id: "u1", username: "alice", role: "member", status: "active" },
      200,
    ),
  });
  draw();
  await fill("alice", "s3cret");

  fireEvent.click(screen.getByRole("button", { name: "登录" }));

  await waitFor(() => expect(calls.some((call) => call.url.endsWith("/auth/login"))).toBe(true));
  const login = calls.find((call) => call.url.endsWith("/auth/login"))!;
  expect(login.init.method).toBe("POST");
  expect(JSON.parse(String(login.init.body))).toEqual({ username: "alice", password: "s3cret" });
});

it("切到注册态提交打到 /api/auth/register", async () => {
  const calls = captureFetch({
    ...UNAUTH_ME,
    "/auth/register": jsonResponse(
      { user_id: "u2", username: "bob", role: "member", status: "pending" },
      201,
    ),
  });
  draw();
  await screen.findByLabelText("用户名");

  fireEvent.click(screen.getByRole("button", { name: /注册/ }));
  await fill("bob", "hunter2");
  fireEvent.click(screen.getByRole("button", { name: "注册" }));

  await waitFor(() => expect(calls.some((call) => call.url.endsWith("/auth/register"))).toBe(true));
  const register = calls.find((call) => call.url.endsWith("/auth/register"))!;
  expect(JSON.parse(String(register.init.body))).toEqual({ username: "bob", password: "hunter2" });
});

it("登录失败直显后端那句话（中文可直读）", async () => {
  captureFetch({
    ...UNAUTH_ME,
    "/auth/login": jsonResponse(
      { code: "unauthenticated", message: "用户名或密码不正确" },
      401,
    ),
  });
  draw();
  await fill("alice", "wrong");

  fireEvent.click(screen.getByRole("button", { name: "登录" }));

  await waitFor(() => expect(screen.getByText("用户名或密码不正确")).toBeTruthy());
});

it("token 只走 cookie：localStorage 里没有它（防倒退）", async () => {
  const calls = captureFetch({
    ...UNAUTH_ME,
    "/auth/login": jsonResponse(
      { user_id: "u1", username: "alice", role: "member", status: "active" },
      200,
    ),
  });
  draw();
  await fill("alice", "s3cret");
  fireEvent.click(screen.getByRole("button", { name: "登录" }));
  await waitFor(() => expect(calls.some((call) => call.url.endsWith("/auth/login"))).toBe(true));

  expect(window.localStorage.getItem("pm_session")).toBeNull();
});

it("密码字段是 type=password（不回显）", async () => {
  captureFetch(UNAUTH_ME);
  draw();

  expect((await screen.findByLabelText("密码")).getAttribute("type")).toBe("password");
});
