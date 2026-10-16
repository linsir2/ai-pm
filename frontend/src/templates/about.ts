import { ROLE_TEXT } from "../labels";
import type { TemplateView } from "../api/types";

/**
 * 模板一行摘要（`tpl-card` 的 about 位）：章节数 + 骨架章计数 + 去重后的角色短名。
 *
 * **为什么不是"按序 role 斜杠连排"**：那是旧实现，标准版会渲染成
 * `13 章 · 骨架章/骨架章/骨架章/骨架章/功能需求/…`——**六个"骨架章"重复六遍**，
 * 一眼看不出这套模板是什么。两个修法：
 *   1. 骨架类章节**计数**（`6 骨架`），它们本来就是"待填的空壳"，重复列没有信息；
 *   2. 其余角色**去重**（按首次出现顺序），最多列 4 个，多了给 `…`——
 *      一行摘要塞 7 项就又变成噪音了（`UI Craft` 的"眯眼测试"同理）。
 * 完整章节清单不靠这个函数：模板库那一屏本来就该列出来。
 */
export function templateAbout(template: TemplateView): string {
  const total = template.sections.length;
  const roles = template.sections.map((section) => ROLE_TEXT[section.role] ?? section.role);
  const backbone = roles.filter((role) => role === "骨架章").length;
  const others: string[] = [];
  for (const role of roles) {
    if (role === "骨架章") continue;
    if (!others.includes(role)) others.push(role);
  }
  const listed = others.slice(0, 4);
  const rest = others.length > listed.length ? " …" : "";
  const parts = [`${total} 章`];
  if (backbone > 0) parts.push(`${backbone} 骨架`);
  if (listed.length > 0) parts.push(listed.join(" · "));
  return `${parts.join(" · ")}${rest}`;
}