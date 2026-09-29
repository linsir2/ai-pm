/* 一个功能子树：名称当标题，整段文字与步骤编号排下来，清单与验收标准并列成表。
   编号只是"排在第几段"，不改变任何后端语义。 */

import type { BlockView } from "../api/types";
import { fieldEntries, groupFields } from "../document/structure";
import { fieldTitle } from "../state/schema";
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
    </article>
  );
}
