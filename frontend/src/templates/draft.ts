/* 模板编辑器的纯函数层（`06` §2.3 / §4）。
 *
 * **组件不做业务判断**（`06` §3.2）：能不能删、能不能选、去哪、叫什么 id，全在这里答。
 * 编辑用的数据形状就是 `GET /templates` 返回的 `sections` 本身——前端不自造结构。
 *
 * 三条规矩在这层守：
 *  ① **章节 id 不跟位置走**：`s{max+1}`，**不填补空缺**。`light` 的
 *     `s1,s2,s4,s5,s11,s13` 跳号是刻意的（standard 的子集，空缺 = 那一章此处不需要），
 *     而 id 又是提示词里给用户看的稳定符号（"你的回答写到 s5"）。
 *  ② **`section_id` → `id`**：GET 与 POST/PUT 的字段名不一致（后端 `_section()` 读 `id`），
 *     转换只发生在 `toPayload` 这一处。
 *  ③ **锚点是结构性事实**：`pickedRoles` 算"还剩哪些 role 没被占"，组件据此置灰下拉与
 *     删除按钮——而不是等提交了再报错（红字在整个编辑过程里会一直亮着）。 */

import type { TemplateSectionPayload, TemplateSectionView } from "../api/types";
import { anchorRoles } from "../state/schema";

/** 三个锚点：真相源在后端 `/schema.template_anchor_roles`（`02` §11.0.70），
 *  这里**只用于算界面状态**——真正把关的是 `DocumentTemplate.validate`。
 *  前端曾经自抄一份 `["feature","open_questions","version_history"]`：后端加第四个锚点时
 *  置灰会静默失效（用户能删掉锚点章，保存才报 409）。副本删掉了。 */
export function isAnchor(section: { role: string }): boolean {
  return anchorRoles().includes(section.role);
}

/** 已经占掉的锚点 role：下拉里这几项要置灰。 */
export function pickedRoles(sections: readonly TemplateSectionView[]): string[] {
  return anchorRoles().filter((role) => sections.some((section) => section.role === role));
}

/** 这个 role 现在还能不能选：非锚点永远能，锚点没被占时能。 */
export function canPickRole(
  sections: readonly TemplateSectionView[],
  role: string,
): boolean {
  if (!anchorRoles().includes(role)) return true;
  return !pickedRoles(sections).includes(role);
}

/** 新一章的 id：取现有最大值 +1。
 *  `s` 前缀之外的东西（模板自己的 id 等）不当数字算；一个都没有时从 s1 起。 */
export function nextSectionId(sections: readonly TemplateSectionView[]): string {
  const highest = sections.reduce((max, section) => {
    const match = /^s(\d+)$/.exec(section.section_id);
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);
  return `s${highest + 1}`;
}

export function addSection(
  sections: readonly TemplateSectionView[],
): TemplateSectionView[] {
  return [
    ...sections,
    {
      section_id: nextSectionId(sections),
      title: "",
      purpose: "",
      role: "plain",
      slots: [],
    },
  ];
}

/** 上移 / 下移一位。已在边上就原样返回一份新数组（不可变，别就地改）。 */
export function moveSection(
  sections: readonly TemplateSectionView[],
  index: number,
  delta: -1 | 1,
): TemplateSectionView[] {
  const target = index + delta;
  if (target < 0 || target >= sections.length) return [...sections];
  const next = [...sections];
  const [moved] = next.splice(index, 1);
  // index 越界时取不到东西——原样返回（调用点按钮本来就 disabled，这里是不变量兜底）
  if (moved === undefined) return [...sections];
  next.splice(target, 0, moved);
  return next;
}

/** 编辑形状 → 后端形状：换字段名，其余原样。
 *  `min_entries` 缺省就不带这个键（后端读不到就是"没声明"，与 GET 回显同义）。
 *  **`coverage` 永远不发**：它不进就绪度、两个内置模板都没声明它，界面上没有它的入口
 *  （`06` §2.3）——带上空值反而会让模板看着像声明过。 */
export function toPayload(
  sections: readonly TemplateSectionView[],
): TemplateSectionPayload[] {
  return sections.map((section) => {
    const payload: TemplateSectionPayload = {
      id: section.section_id,
      title: section.title,
      purpose: section.purpose,
      role: section.role,
      slots: section.slots.map((slot) => ({ ...slot })),
    };
    if (section.min_entries !== undefined && section.min_entries !== null) {
      payload.min_entries = section.min_entries;
    }
    return payload;
  });
}

/** 提交前的本地提示。**只报 UI 级问题**——后端那些（锚点恰各一、判据 ≥ 1）由界面结构
 *  挡住（置灰 / 不显示 / `min=1`），不在这里复刻一份。 */
export function validateDraft(
  title: string,
  sections: readonly TemplateSectionView[],
): string[] {
  const problems: string[] = [];
  if (title.trim() === "") problems.push("模板标题不能空");
  for (const section of sections) {
    if (section.title.trim() === "") {
      problems.push(`${section.section_id}：标题不能空`);
    }
    const names = section.slots.map((slot) => slot.name);
    if (new Set(names).size !== names.length) {
      problems.push(`${section.section_id}：槽位名重复`);
    }
  }
  return problems;
}

/** "从零建"的初始草稿：预置三个锚点章（`06` §2.3）。
 *
 *  **为什么不是空白列表**：空白列表的用户得先知道"哪个 role 是必须留的"才过得了校验，
 *  而那是产品的硬规则、不该让人去猜。预置之后任何时候点保存都不会因为"缺锚点"失败。 */
export function blankDraft(): TemplateSectionView[] {
  return [
    {
      section_id: "s1",
      title: "功能需求",
      purpose: "每个功能一棵子树：它给谁解决什么问题，怎么验收。",
      role: "feature",
      slots: [],
    },
    {
      section_id: "s2",
      title: "风险与开放问题",
      purpose: "还没定的事与可能出事的地方，别在正文里偷偷混过去。",
      role: "open_questions",
      slots: [],
    },
    {
      section_id: "s3",
      title: "版本历史",
      purpose: "不用手写——导出时由版本表生成这一章。",
      role: "version_history",
      slots: [],
    },
  ];
}