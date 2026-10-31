import { describe, expect, it } from "vitest";

import type { PendingAction } from "./pending";
import { pendingActionLabel, pendingBlockedMessage } from "./pending";

describe("在途动作的人话", () => {
  it("调研 / 研究来源 / 确认写入各有自己的说法", () => {
    expect(pendingActionLabel("exploration")).toContain("调研");
    expect(pendingActionLabel("research")).toContain("研究");
    expect(pendingActionLabel("confirm")).toContain("确认写入");
  });

  it("每个既有动作都有说法，不会掉到兜底", () => {
    const actions: PendingAction[] = [
      "start",
      "opening",
      "answers",
      "confirm",
      "retry",
      "dig",
      "edit",
      "material",
      "round_material",
      "memory",
      "exploration",
      "research",
      "discussion",
      "simulation",
      "sim_review",
      "web_request",
    ];
    for (const action of actions) {
      expect(pendingActionLabel(action)).not.toBe("正在处理…");
    }
  });

  it("未知值保守成「正在处理…」", () => {
    expect(pendingActionLabel("no_such_action")).toBe("正在处理…");
    expect(pendingActionLabel(null)).toBe("正在处理…");
  });

  it("busy 提示用当前在途动作，而不是被拦下的那一个", () => {
    expect(pendingBlockedMessage("research")).toMatch(/^当前正在.*，完成后再试$/);
    expect(pendingBlockedMessage("research")).toContain("研究");
    expect(pendingBlockedMessage(undefined)).toBe("当前正在处理，完成后再试");
  });
});
