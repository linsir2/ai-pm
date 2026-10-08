// @vitest-environment jsdom
/* 选区把手（M13）：默认收起；触屏（coarse pointer）整个不渲染。
   选区的真实行为（出现/点击/换章）靠浏览器手测——jsdom 的 Range 没有 rect，测不了定位。 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscussionScope } from "./DiscussionContext";
import { DiscussHandle } from "./DiscussHandle";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function draw() {
  return render(
    <DiscussionScope value={{ canStart: true, start: vi.fn() }}>
      <div className="doc__inner">
        <DiscussHandle sectionKey="s1" />
      </div>
    </DiscussionScope>,
  );
}

describe("DiscussHandle", () => {
  it("默认收起（没有选区就不存在）：按钮在 DOM 里但 hidden", () => {
    const { container } = draw();

    const handle = container.querySelector(".discuss-handle");
    expect(handle).not.toBeNull();
    expect(handle!.hasAttribute("hidden")).toBe(true);
    expect(handle!.getAttribute("aria-hidden")).toBe("true");
    expect(handle!.getAttribute("tabindex")).toBe("-1");
  });

  it("触屏（coarse pointer）不渲染把手——等价入口在条目工具行", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    );
    const { container } = draw();

    expect(container.querySelector(".discuss-handle")).toBeNull();
  });
});
