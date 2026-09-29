/* 一个功能子树：名称当标题，整段文字与步骤编号排下来，清单与验收标准并列成表。
   编号只是"排在第几段"，不改变任何后端语义。 */

import { useState } from "react";

import type { BlockView } from "../api/types";
import { fieldEntries, groupFields } from "../document/structure";
import { featureFields, fieldTitle } from "../state/schema";
import { useEdit } from "./EditContext";
import { EntryList } from "./EntryList";
import { EvidenceChip } from "./EvidenceChip";

export function FeatureBlock({
  feature,
  justWritten,
}: {
  feature: BlockView;
  justWritten: string[];
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

  return (
    <article
      className={isNew ? "feature feature--just-written" : "feature"}
      id={`block-${feature.block_id}`}
    >
      <header className="feature__head">
        <span className="feature__slug">feature#{slug}</span>
        <h3 className="feature__name">{name ?? "还没写名称"}</h3>
        {nameEntries.length > 0 && <EvidenceChip eids={nameEntries[0]!.refs} />}
        {feature.manually_edited && <span className="tag">手改过</span>}
      </header>

      {sections.length === 0 && grid.length === 0 && (
        <p className="section__blank">这个功能还没有字段内容。</p>
      )}

      {sections.length > 0 && (
        <ol className="fields">
          {sections.map((block, index) => (
            <li className="field" id={`block-${block.block_id}`} key={block.block_id}>
              <h4 className="field__name">
                <span className="field__num mono">{index + 1}</span>
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
