// @vitest-environment jsdom
/* 顶层装配：主题必须在**登录之前**就落到 <html> 上。
   这条不是审美偏好，是缺陷修复的钉子——主题 state 原本住在 `Workspace` 里，
   而 `Workspace` 登录后才渲染，于是用暗色的人登出后看到的是一片亮屏。
   mock fetch 体例照抄 `LoginView.test.tsx`：经公开入口打桩，不 mock 模块。 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it } from "vitest";

import App from "./App";

const originalFetch = globalThis.fetch;

function stubFetch(): void {
  globalThis.fetch = (async (url: unknown) => {
    // 未登录：/auth/me 一律 401，其余不关心
    const isMe = String(url).endsWith("/auth/me");
    return {
      ok: !isMe,
      status: isMe ? 401 : 200,
      statusText: isMe ? "Unauthorized" : "OK",
      json: async () => (isMe ? {} : {}),
      text: async () => "{}",
    } as Response;
  }) as unknown as typeof fetch;
}

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
  stubFetch();
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

it("未登录时也把存下来的暗色主题落到 html 上（登录屏别突然变亮）", async () => {
  window.localStorage.setItem("pmstudio-theme", "dark");

  render(<App />);

  await screen.findByLabelText("用户名");
  await waitFor(() =>
    expect(document.documentElement.dataset.theme).toBe("dark"),
  );
});

it("没有存过主题就不强加（保持默认亮色）", async () => {
  render(<App />);

  await screen.findByLabelText("用户名");
  expect(document.documentElement.dataset.theme).not.toBe("dark");
});