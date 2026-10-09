// @vitest-environment node
/* 设计令牌守卫：`app.css` 引用的每个 `var(--x)` 都必须在 `tokens.css` 有定义。
   为什么要有：`--muted`/`--accent` 曾被引用却从未定义，属性静默失效、颜色靠继承兜底——
   这类 bug 肉眼难查，用一条守卫钉死，以后拼错变量名会在 CI 变红。

   只算**没有 fallback** 的 `var(--x)`：带 fallback（`var(--x, 兜底)`）的缺定义不影响渲染。 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, "");
}

/** 收集 CSS 里定义的自定义属性（`--x:`）。 */
function definedTokens(css: string): Set<string> {
  const found = new Set<string>();
  for (const match of stripComments(css).matchAll(/(--[a-z0-9-]+)\s*:/gi)) {
    found.add(match[1]!);
  }
  return found;
}

/** 收集 CSS 里引用的、**无 fallback** 的自定义属性（`var(--x)`）。 */
function usedTokens(css: string): Set<string> {
  const found = new Set<string>();
  for (const match of stripComments(css).matchAll(/var\(\s*(--[a-z0-9-]+)\s*\)/gi)) {
    found.add(match[1]!);
  }
  return found;
}

describe("设计令牌", () => {
  it("app.css 里每个 var(--x)（无 fallback）都在 tokens.css 有定义", () => {
    const defined = definedTokens(readFileSync(resolve(here, "tokens.css"), "utf8"));
    const used = usedTokens(readFileSync(resolve(here, "app.css"), "utf8"));
    const missing = [...used].filter((token) => !defined.has(token)).sort();
    expect(missing).toEqual([]);
  });
});
