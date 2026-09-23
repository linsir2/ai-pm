/* 从文档投影里推出界面要用的结构：功能子树一览、功能内部的字段分组。

   只做"换个摆法"，不做判断：字段的中文名、字段该排在正文还是表格里，
   依据都来自后端 block 的 `field` 与 `kind`（`domain/authoring/schema.py` 是真相源）。 */

import type { BlockView, DocumentView, Entry } from "../api/types";
import { fieldTitle } from "../labels";

export const FEATURE_HOME = "s5";

export interface FeatureSummary {
  blockId: string;
  slug: string;
  /** 「名称」字段的第一条。没写就空着——不替它编。 */
  name: string;
  oneLiner: string | null;
  block: BlockView;
}

export function listFeatures(doc: DocumentView | null): FeatureSummary[] {
  if (doc === null) return [];
  const home = doc.sections.find((section) => section.section_id === FEATURE_HOME);
  if (home === undefined) return [];

  const walk = (blocks: BlockView[]): FeatureSummary[] =>
    blocks.flatMap((block) => {
      if (block.kind === "feature") {
        return [
          {
            blockId: block.block_id,
            slug: block.feature_slug ?? "未命名",
            name: firstText(fieldEntries(block, "name")) ?? block.feature_slug ?? "未命名",
            oneLiner: firstText(fieldEntries(block, "one_liner")),
            block,
          },
        ];
      }
      return walk(block.children);
    });

  return walk(home.blocks);
}

export function fieldBlock(feature: BlockView, field: string): BlockView | null {
  return feature.children.find((child) => child.field === field) ?? null;
}

export function fieldEntries(feature: BlockView, field: string): Entry[] {
  return fieldBlock(feature, field)?.entries ?? [];
}

export interface FieldGroup {
  /** 一段一段读的：整段文字与步骤。 */
  sections: BlockView[];
  /** 并列看更清楚的：清单与验收标准。 */
  grid: BlockView[];
}

export function groupFields(feature: BlockView): FieldGroup {
  const fields = feature.children.filter((child) => child.field !== null);
  return {
    sections: fields.filter((child) => child.kind === "text" || child.kind === "steps"),
    grid: fields.filter((child) => child.kind !== "text" && child.kind !== "steps"),
  };
}

/** 页内锚点：一章里能跳的地方（功能 → 字段）。 */
export interface Anchor {
  id: string;
  text: string;
}

export function chapterAnchors(doc: DocumentView | null, sectionId: string): Anchor[] {
  if (doc === null) return [];
  const section = doc.sections.find((item) => item.section_id === sectionId);
  if (section === undefined) return [];

  return section.blocks.flatMap((block) => {
    if (block.kind !== "feature") return [];
    return [
      { id: `block-${block.block_id}`, text: block.feature_slug ?? "功能" },
      ...block.children
        .filter((child) => child.field !== null)
        .map((child) => ({
          id: `block-${child.block_id}`,
          text: fieldTitle(child.field) ?? child.field ?? "",
        })),
    ];
  });
}

function firstText(entries: Entry[]): string | null {
  const hit = entries.find((entry) => entry.text.trim() !== "");
  return hit === undefined ? null : hit.text;
}
