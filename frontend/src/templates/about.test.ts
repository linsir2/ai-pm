import { expect, it } from "vitest";
import { templateAbout } from "./about";

const sec = (id: string, role: string) => ({
  section_id: id, title: id, purpose: "", role, slots: [],
});

it("骨架章计数，不重复列出", () => {
  // 旧实现会渲染成 "2 章 · 骨架章/骨架章" —— 重复即噪音
  expect(templateAbout({
    template_id: "light", title: "轻量版",
    sections: [sec("s1", "backbone"), sec("s2", "backbone")],
  })).toBe("2 章 · 2 骨架");
});

it("骨架 + 其余角色混排", () => {
  expect(templateAbout({
    template_id: "light", title: "轻量版",
    sections: [sec("s1", "backbone"), sec("s5", "feature")],
  })).toBe("2 章 · 1 骨架 · 功能需求");
});

it("同角色去重，按首次出现顺序", () => {
  expect(templateAbout({
    template_id: "x", title: "x",
    sections: [sec("a", "backbone"), sec("b", "feature"), sec("c", "feature")],
  })).toBe("3 章 · 1 骨架 · 功能需求");
});

it("超过 4 个不同角色就截断并给省略号", () => {
  expect(templateAbout({
    template_id: "standard", title: "标准版",
    sections: [
      sec("s1", "backbone"), sec("s5", "feature"), sec("s6", "steps"),
      sec("s7", "data"), sec("s9", "decision"), sec("s11", "open_questions"),
    ],
  })).toBe("6 章 · 1 骨架 · 功能需求 · 流程步骤 · 数据与接口 · 关键决策 …");
});

it("未知 role 回落原文", () => {
  expect(templateAbout({
    template_id: "x", title: "x",
    sections: [sec("s1", "weird")],
  })).toBe("1 章 · weird");
});

it("没有骨架章就不出现'骨架'这个词", () => {
  expect(templateAbout({
    template_id: "x", title: "x",
    sections: [sec("s5", "feature"), sec("s13", "version_history")],
  })).toBe("2 章 · 功能需求 · 版本历史");
});
