import type { BlockView, Entry } from "../api/types";
import { fieldTitle } from "../labels";
import { EvidenceChip } from "./EvidenceChip";

/** block 树：功能子树有层级，字段块带字段名，条目自带出处编号。 */
export function BlockTree({
  block,
  justWritten,
}: {
  block: BlockView;
  justWritten: string[];
}) {
  const isNew = justWritten.includes(block.block_id);
  const className = isNew ? "block block--just-written" : "block";

  if (block.kind === "feature") {
    return (
      <div className={`${className} feature`}>
        <div className="feature__head">
          <span className="feature__slug">feature#{block.feature_slug ?? "未命名"}</span>
        </div>
        <div className="feature__children">
          {block.children.map((child) => (
            <BlockTree block={child} key={child.block_id} justWritten={justWritten} />
          ))}
        </div>
      </div>
    );
  }

  const field = fieldTitle(block.field);
  return (
    <div className={className}>
      {(field !== null || block.manually_edited) && (
        <div className="field__name">
          {field}
          {block.manually_edited && <span className="tag">手改过</span>}
        </div>
      )}
      <Entries entries={block.entries} kind={block.kind} />
      {block.children.map((child) => (
        <BlockTree block={child} key={child.block_id} justWritten={justWritten} />
      ))}
    </div>
  );
}

function Entries({ entries, kind }: { entries: Entry[]; kind: string }) {
  if (entries.length === 0) return null;

  if (kind === "steps") {
    return (
      <ol className="entries entries--steps">
        {entries.map((entry) => (
          <li className="entry" key={entry.entry_id}>
            <span className="entry__text">{entry.text}</span>{" "}
            <EvidenceChip eids={entry.refs} />
          </li>
        ))}
      </ol>
    );
  }

  return (
    <ul className="entries">
      {entries.map((entry) => (
        <li className="entry" key={entry.entry_id}>
          <span className="entry__text">{entry.text}</span>
          <span className="entry__refs">
            <EvidenceChip eids={entry.refs} />
          </span>
        </li>
      ))}
    </ul>
  );
}
