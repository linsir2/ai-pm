/* 一个功能 = 一张卡片（FR-NN）：名称 + 状态（必填字段齐不齐）+ 字段内容 + 证据链。
   编号只是"排在第几个"，不改变任何后端语义；状态用 schema 的 required 口径算，真数据。 */

import { useState } from "react";

import type { BlockView, RoundView } from "../api/types";
import { fieldEntries, groupFields } from "../document/structure";
import { featureFields, fieldTitle } from "../state/schema";
import { EntryList } from "./EntryList";
import { EvidenceChain } from "./EvidenceChain";
import { EvidenceChip } from "./EvidenceChip";
import { useEdit } from "./EditContext";

export function FeatureBlock({
  feature,
  justWritten,
  index = 0,
  round = null,
}: {
  feature: BlockView;
  justWritten: string[];
  /** 在 s5 里的展示序号（FR-01）——纯显示顺序。 */
  index?: number;
  /** 证据链查材料用（本轮）。 */
  round?: RoundView | null;
}) {
  const slug = feature.feature_slug ?? "未命名";
  const nameEntries = fieldEntries(feature, "name");
  const name = nameEntries[0]?.text ?? null;
  const group = groupFields(feature);
  const sections = group.sections.filter((block) => block.field !== "name");
  const grid = group.grid.filter((block) => block.field !== "name");
  const isNew = justWritten.includes(feature.block_id);
  const edit = useEdit();
  const [adding, setAdding] = useState<{ field: string; text: string } | null>(null);
  // 这一棵功能"还没写"的字段（含字段块根本不存在的那种）——补一个块就靠它（F8-①）
  const written = new Set(feature.children.map((child) => child.field));
  const missing = featureFields().filter((item) => !written.has(item.name));

  // 状态：必填字段都有非空条目 = 已确认；缺任何一个 = 待补充（与后端 feature_gaps 同口径）
  const fieldHasEntries = (field: string) => {
    const block = feature.children.find((child) => child.field === field);
    return block !== undefined && block.entries.some((entry) => entry.text.trim() !== "");
  };
  const missingRequired = featureFields().filter(
    (item) => item.required && !fieldHasEntries(item.name),
  );
  const confirmed = missingRequired.length === 0;

  // 证据链：全部字段的编号，去重保序
  const refs: string[] = [];
  for (const child of feature.children) {
    for (const entry of child.entries) {
      for (const ref of entry.refs) {
        if (!refs.includes(ref)) refs.push(ref);
      }
    }
  }

  return (
    <article
      className={isNew ? "fcard fcard--new" : "fcard"}
      id={`block-${feature.block_id}`}
    >
      <header className="fcard__head">
        <span className="fcard__num mono" title={`feature#${slug}`}>
          FR-{String(index + 1).padStart(2, "0")}
        </span>
        <h3 className="fcard__name">{name ?? "还没写名称"}</h3>
        <span
          className={confirmed ? "fcard__status fcard__status--ok" : "fcard__status fcard__status--todo"}
          title={
            confirmed
              ? "必填字段都写了"
              : `还缺：${missingRequired.map((item) => item.title).join("、")}`
          }
        >
          {confirmed ? "已确认" : "待补充"}
        </span>
        {feature.manually_edited && <span className="tag">手改过</span>}
      </header>

      {sections.length === 0 && grid.length === 0 && (
        <p className="section__blank">这个功能还没有字段内容。</p>
      )}

      {sections.length > 0 && (
        <ol className="fields">
          {sections.map((block, order) => (
            <li className="field" id={`block-${block.block_id}`} key={block.block_id}>
              <h4 className="field__name">
                <span className="field__num mono">{order + 1}</span>
                {fieldTitle(block.field)}
                <EvidenceChip eids={block.entries.flatMap((entry) => entry.refs)} />
              </h4>
              <EntryList
                blockId={block.block_id}
                entries={block.entries}
                kind={block.kind}
                multiple={block.multiple}
              />
            </li>
          ))}
        </ol>
      )}

      {grid.length > 0 && (
        <div className="keygrid">
          {grid.map((block) => (
            <section className="keygrid__cell" id={`block-${block.block_id}`} key={block.block_id}>
              <h4 className="field__name">{fieldTitle(block.field)}</h4>
              <EntryList
                blockId={block.block_id}
                entries={block.entries}
                kind={block.kind}
                multiple={block.multiple}
                compact
              />
            </section>
          ))}
        </div>
      )}

      {refs.length > 0 && <EvidenceChain refs={refs} round={round} />}

      {edit.canEdit && missing.length > 0 && (
        <div className="feature__add">
          {adding === null ? (
            <button
              type="button"
              className="entry-add"
              onClick={() => setAdding({ field: missing[0]!.name, text: "" })}
            >
              + 补一个还没写的字段
            </button>
          ) : (
            <form
              className="feature__addform"
              onSubmit={(event) => {
                event.preventDefault();
                if (adding.text.trim() === "") return;
                edit.addField(slug, adding.field, adding.text);
                setAdding(null);
              }}
            >
              <select
                className="select"
                aria-label="补哪个字段"
                value={adding.field}
                onChange={(event) => setAdding({ ...adding, field: event.target.value })}
              >
                {missing.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.title}
                  </option>
                ))}
              </select>
              <input
                className="input"
                aria-label="字段内容"
                placeholder="写一条，保存后就进文档（并产生新的一版）"
                value={adding.text}
                autoFocus
                onChange={(event) => setAdding({ ...adding, text: event.target.value })}
              />
              <button type="submit" className="btn btn--primary btn--sm" disabled={edit.busy}>
                加进文档
              </button>
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={() => setAdding(null)}
              >
                取消
              </button>
            </form>
          )}
        </div>
      )}
    </article>
  );
}
