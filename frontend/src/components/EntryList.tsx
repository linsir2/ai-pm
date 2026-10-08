/* 条目列表：文档里每一条内容都从这里进出。
   一条 = 文本 + 出处胶囊 + （可编辑时）改 / 删；列表末尾是"加一条"。
   改与删只调 `EditContext`，不在这里发请求。 */

import { useId, useState } from "react";

import type { Entry } from "../api/types";
import { useDiscussion } from "./DiscussionContext";
import { useEdit } from "./EditContext";
import { useExploration } from "./ExplorationContext";
import { EvidenceChip } from "./EvidenceChip";

export function EntryList({
  blockId,
  entries,
  kind = "bullet_list",
  multiple = true,
  compact = false,
  addLabel = "+ 加一条",
}: {
  blockId: string;
  entries: Entry[];
  kind?: string;
  multiple?: boolean;
  compact?: boolean;
  addLabel?: string;
}) {
  const edit = useEdit();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const ordered = kind === "steps";
  const listClass = compact ? "entries entries--compact" : "entries";
  const className = ordered ? `${listClass} entries--steps` : listClass;
  const canAdd = edit.canEdit && multiple;

  const rows = entries.map((entry) => (
    <EntryRow
      key={entry.entry_id}
      blockId={blockId}
      entry={entry}
      canEdit={edit.canEdit}
      editing={editingId === entry.entry_id}
      onStartEdit={() => setEditingId(entry.entry_id)}
      onStopEdit={() => setEditingId(null)}
    />
  ));

  const addRow = canAdd && (
    <li className="entry entry--add">
      {adding ? (
        <EntryForm
          initial=""
          placeholder="写一条，保存后就进文档（并产生新的一版）"
          submitLabel="加进文档"
          onSubmit={(text) => {
            edit.add(blockId, text);
            setAdding(false);
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <button type="button" className="entry-add" onClick={() => setAdding(true)}>
          {addLabel}
        </button>
      )}
    </li>
  );

  return (
    <div className="entries-wrap">
      {ordered ? (
        <ol className={className}>
          {rows}
          {addRow}
        </ol>
      ) : (
        <ul className={className}>
          {rows}
          {addRow}
        </ul>
      )}
      {entries.length === 0 && !canAdd && <p className="section__blank">还没写</p>}
    </div>
  );
}

function EntryRow({
  blockId,
  entry,
  canEdit,
  editing,
  onStartEdit,
  onStopEdit,
}: {
  blockId: string;
  entry: Entry;
  canEdit: boolean;
  editing: boolean;
  onStartEdit: () => void;
  onStopEdit: () => void;
}) {
  const edit = useEdit();
  const research = useExploration();
  const discussion = useDiscussion();
  const [armed, setArmed] = useState(false);

  if (editing) {
    return (
      <li className="entry entry--editing">
        <EntryForm
          initial={entry.text}
          placeholder=""
          submitLabel="保存"
          onSubmit={(text) => {
            edit.edit(blockId, entry.entry_id, text);
            onStopEdit();
          }}
          onCancel={onStopEdit}
        />
      </li>
    );
  }

  return (
    // 锚点：跨轮编号跳转要"滚到引用它的那条内容"（id 由 entry_id 来，稳定）
    <li className="entry" id={`entry-${entry.entry_id}`}>
      <span className="entry__text">{entry.text}</span>
      <span className="entry__refs">
        {(canEdit || research.canStart || discussion.canStart) && (
          <span className="entry__tools">
            {discussion.canStart && (
              <button
                type="button"
                className="iconbtn"
                onClick={() => discussion.start(entry.text)}
                aria-label="讨论这一条"
                title="就这一条开一条讨论（切到讨论页签）"
              >
                讨论
              </button>
            )}
            {research.canStart && (
              <button
                type="button"
                className="iconbtn"
                onClick={() =>
                  research.start({ kind: "entry", ref: entry.entry_id, focus: entry.text })
                }
                aria-label="调研这一条"
                title="拿这一条去调研（只出待过目的候选，不改文档）"
              >
                调研
              </button>
            )}
            {canEdit && (
              <>
                <button
                  type="button"
                  className="iconbtn"
                  onClick={onStartEdit}
                  aria-label="改这一条"
                  title="改这一条（会记一版）"
                >
                  改
                </button>
                {armed ? (
                  <button
                    type="button"
                    className="iconbtn iconbtn--danger"
                    onClick={() => edit.remove(blockId, entry.entry_id)}
                    onBlur={() => setArmed(false)}
                    title="再点一次就真的删掉"
                  >
                    确认删
                  </button>
                ) : (
                  <button
                    type="button"
                    className="iconbtn"
                    onClick={() => setArmed(true)}
                    aria-label="删这一条"
                    title="删这一条（会记一版）"
                  >
                    删
                  </button>
                )}
              </>
            )}
          </span>
        )}
        <EvidenceChip eids={entry.refs} />
      </span>
    </li>
  );
}

function EntryForm({
  initial,
  placeholder,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial: string;
  placeholder: string;
  submitLabel: string;
  onSubmit: (text: string) => void;
  onCancel: () => void;
}) {
  const edit = useEdit();
  const [text, setText] = useState(initial);
  const fieldId = useId();
  const canSave = text.trim() !== "" && !edit.busy;

  return (
    <form
      className="entry__form"
      onSubmit={(event) => {
        event.preventDefault();
        if (canSave) onSubmit(text.trim());
      }}
    >
      <label className="sr-only" htmlFor={fieldId}>
        条目内容
      </label>
      <textarea
        id={fieldId}
        className="entry__editor"
        value={text}
        autoFocus
        placeholder={placeholder}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Escape") onCancel();
          if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && canSave) {
            onSubmit(text.trim());
          }
        }}
      />
      <div className="entry__actions">
        <button type="submit" className="btn btn--sm btn--primary" disabled={!canSave}>
          {edit.busy ? "正在写…" : submitLabel}
        </button>
        <button type="button" className="btn btn--sm btn--ghost" onClick={onCancel}>
          取消
        </button>
        <span className="muted small">Ctrl / ⌘ + Enter 保存 · 一次改动一版</span>
      </div>
    </form>
  );
}
