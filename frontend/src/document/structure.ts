/* 从文档投影里推出界面要用的结构：功能子树一览、功能内部的字段分组。

   只做"换个摆法"，不做判断：字段的中文名、字段该排在正文还是表格里，
   依据都来自后端 block 的 `field` 与 `kind`（`domain/authoring/schema.py` 是真相源）。 */

import type { BlockView, DocumentView, Entry } from "../api/types";
import type { TargetRequest } from "../api/client";
import { parseTarget } from "../labels";
import { fieldTitle } from "../state/schema";

/** 功能子树挂在哪一章（`02` §11.0.69）：**看块的结构**，不认章节 id。
 *
 *  `s5` 只是 `standard` 那套模板的锚点 id——自定义模板的功能章可以叫 `feat`、`功能`…
 *  这里找"顶层带 feature 块的章"，找不到就 null（不猜，调用方说实话）。 */
export function featureHome(doc: DocumentView | null): string | null {
  if (doc === null) return null;
  return doc.sections.find((section) => section.blocks.some((b) => b.kind === "feature"))
    ?.section_id ?? null;
}

/** 手改"加一个功能字段"要发去处的 target：章节取功能锚点（`App.tsx` 用）。
 *
 *  找不到功能章就 null——发一个 s5 去处在自定义模板下会被后端拒（未知章节），
 *  与其发一个必失败的请求，不如让调用方说清楚。 */
export function featureFieldTarget(
  doc: DocumentView | null,
  featureSlug: string,
  field: string,
): TargetRequest | null {
  const home = featureHome(doc);
  if (home === null) return null;
  return { zone: "body", section_id: home, feature_slug: featureSlug, field };
}

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
  const homeId = featureHome(doc);
  if (homeId === null) return [];
  const home = doc.sections.find((section) => section.section_id === homeId);
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

/** 一条内容落在哪一章（点编号想滚到那条内容时，得先切到它那一章）。

    只做"换个摆法"：顺着投影走一遍，不做判断、不猜。 */
export function sectionOfEntry(doc: DocumentView | null, entryId: string): string | null {
  if (doc === null) return null;
  if (doc.preference_entries.some((entry) => entry.entry_id === entryId)) {
    return "preference";
  }
  const walk = (blocks: BlockView[]): boolean =>
    blocks.some(
      (block) =>
        block.entries.some((entry) => entry.entry_id === entryId) || walk(block.children),
    );
  for (const section of doc.sections) {
    if (walk(section.blocks)) return section.section_id;
  }
  return null;
}

/** 去处 → 文档落点（点去处跳转的唯一解析）。

    回落链不跳空：功能字段 → 字段块 → 功能块 → 章；章节槽 → 槽块 → 章；
    形状不认 / 章不在文档里 → null（调用方说实话，不假跳）。 */
export function resolveTarget(
  doc: DocumentView | null,
  label: string,
): { sectionId: string; blockId: string | null } | null {
  if (doc === null) return null;
  const parsed = parseTarget(label);
  if (parsed === null) return null;
  if (parsed.zone === "preference") return { sectionId: "preference", blockId: null };

  const section = doc.sections.find((item) => item.section_id === parsed.sectionId);
  if (section === undefined) return null;

  if (parsed.slug !== null) {
    const feature = listFeatures(doc).find((item) => item.slug === parsed.slug);
    if (feature === undefined) return { sectionId: parsed.sectionId, blockId: null };
    if (parsed.field !== null) {
      const field = fieldBlock(feature.block, parsed.field);
      if (field !== null) return { sectionId: parsed.sectionId, blockId: field.block_id };
    }
    return { sectionId: parsed.sectionId, blockId: feature.blockId };
  }

  if (parsed.field !== null) {
    const slot = section.blocks.find((block) => block.field === parsed.field);
    if (slot !== undefined) return { sectionId: parsed.sectionId, blockId: slot.block_id };
  }
  return { sectionId: parsed.sectionId, blockId: null };
}
