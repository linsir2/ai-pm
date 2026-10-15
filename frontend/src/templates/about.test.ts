import { expect, it } from "vitest";
import { templateAbout } from "./about";

it("章节数 + 按序 role 短名", () => {
  expect(templateAbout({
    template_id: "light", title: "轻量版",
    sections: [
      { section_id: "s1", title: "背景", purpose: "", role: "backbone", slots: [] },
      { section_id: "s5", title: "功能", purpose: "", role: "feature", slots: [] },
    ],
  })).toBe("2 章 · 骨架章/功能需求");
});

it("未知 role 回落原文", () => {
  expect(templateAbout({
    template_id: "x", title: "x",
    sections: [{ section_id: "s1", title: "a", purpose: "", role: "weird", slots: [] }],
  })).toBe("1 章 · weird");
});
