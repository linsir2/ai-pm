import { ROLE_TEXT } from "../labels";
import type { TemplateView } from "../api/types";

/** 模板一行摘要（tpl-card 的 about 位）：章节数 + 按序 role 短名。 */
export function templateAbout(template: TemplateView): string {
  const roles = template.sections.map((section) => ROLE_TEXT[section.role] ?? section.role);
  return `${template.sections.length} 章 · ${roles.join("/")}`;
}
