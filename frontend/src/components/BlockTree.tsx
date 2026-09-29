import type { BlockView } from "../api/types";
import { fieldTitle } from "../state/schema";
import { EntryList } from "./EntryList";

/** 非功能块：条目 + 出处 + （可编辑时）改 / 加 / 删。 */
export function BlockTree({
  block,
  justWritten,
}: {
  block: BlockView;
  justWritten: string[];
}) {
  const isNew = justWritten.includes(block.block_id);
  // 后端已经给了字段中文名（含 s8 的六个章节槽），本地表只作老数据兜底
  const field = block.field_title ?? fieldTitle(block.field);
  const isSlot = block.field !== null && block.feature_slug === null;
  const slotEmpty = isSlot && block.entries.length === 0;

  return (
    <div className={isNew ? "block block--just-written" : "block"}>
      {(field !== null || block.manually_edited) && (
        <div className="field__name">
          {field}
          {block.manually_edited && <span className="tag">手改过</span>}
          {slotEmpty && <span className="tag">待补</span>}
        </div>
      )}
      <EntryList
        blockId={block.block_id}
        entries={block.entries}
        kind={block.kind}
        multiple={block.multiple}
        // 章节级的块没有字段名：在这里加一条 = 这一章新开一段
        addLabel={block.field === null ? "+ 新开一段" : "+ 加一条"}
      />
      {block.children.map((child) => (
        <BlockTree block={child} key={child.block_id} justWritten={justWritten} />
      ))}
    </div>
  );
}
