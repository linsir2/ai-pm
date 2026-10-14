// @vitest-environment jsdom
/* 选段动作条（M17，前身是 M13「就这段聊聊」把手）：默认收起；全输入设备可用
   （不再 aria-hidden、触屏也渲染）；选区的真实行为（出现/点击/换章）靠浏览器手测——
   jsdom 的 Range 没有 rect，测不了定位。 */

import { cleanup, render } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DiscussionScope } from "./DiscussionContext";
import { ExplorationScope } from "./ExplorationContext";
import { SelectionBar } from "./SelectionBar";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const here = dirname(fileURLToPath(import.meta.url));

function draw(discussionCan = true, researchCan = true) {
  return render(
    <DiscussionScope value={{ canStart: discussionCan, start: vi.fn() }}>
      <ExplorationScope value={{ canStart: researchCan, start: vi.fn() }}>
        <div className="doc__inner">
          <SelectionBar sectionKey="s1" />
        </div>
      </ExplorationScope>
    </DiscussionScope>,
  );
}

describe("SelectionBar", () => {
  it("S1 默认收起（没有选区就不存在）：容器在 DOM 里但 hidden", () => {
    const { container } = draw();

    const bar = container.querySelector(".selbar");
    expect(bar).not.toBeNull();
    expect(bar!.hasAttribute("hidden")).toBe(true);
  });

  it("S2 可访问：不是 aria-hidden，两个动作都可聚焦", () => {
    const { container } = draw();

    const bar = container.querySelector(".selbar")!;
    expect(bar.getAttribute("aria-hidden")).toBeNull();
    expect(bar.getAttribute("role")).toBe("group");
    const buttons = [...bar.querySelectorAll("button")];
    expect(buttons.map((button) => button.textContent)).toEqual(["添加到对话", "调研这段"]);
    for (const button of buttons) {
      expect(button.getAttribute("tabindex")).toBeNull();
    }
  });

  it("S3 触屏（coarse pointer）也渲染——不再整块不渲染", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }),
    );
    const { container } = draw();

    expect(container.querySelector(".selbar")).not.toBeNull();
  });

  it("S4 门控：单关对应禁用，双关不渲染", () => {
    const one = draw(true, false);
    const bar = one.container.querySelector(".selbar")!;
    const buttons = [...bar.querySelectorAll("button")] as HTMLButtonElement[];
    expect(buttons).toHaveLength(2);
    expect(buttons[0]!.disabled).toBe(false);
    expect(buttons[1]!.disabled).toBe(true);
    cleanup();

    const none = draw(false, false);
    expect(none.container.querySelector(".selbar")).toBeNull();
  });

  it("样式守卫：浮条不再被断点隐藏（hidden 属性除外），旧类名已删", () => {
    const css = readFileSync(resolve(here, "../styles/app.css"), "utf8");

    expect(css).not.toMatch(/\.discuss-handle/);
    const blocks = [...css.matchAll(/\.selbar(\[[^\]]*\])?\s*\{[^}]*\}/g)];
    const visible = blocks.filter((match) => match[1] === undefined);
    expect(visible.length).toBeGreaterThan(0);
    for (const match of visible) {
      expect(match[0]).not.toMatch(/display\s*:\s*none/);
    }
  });
});
