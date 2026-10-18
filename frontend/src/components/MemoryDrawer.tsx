/* 记忆抽屉（M6）：这个项目**确认过**的跨轮记忆——教训 / 已定口径 / 已否掉。

   两条设计口径：
   ① 它是**只读列表 + 单条删除**：批量改（合并 / 新增）只走草案卡上的"这一轮攒下的记忆"，
      因为那些要 AI 先提议、人再逐条勾；
   ② 删是**立刻生效**的（下一轮提示词就不带它了）——记错一条比不记更麻烦，
      所以"看得见也删得掉"是这套东西能被信任的前提。 */

import { useEffect, useState } from "react";

import { api } from "../api/client";
import type { MemoryEntryView } from "../api/types";
import { EvidenceChip } from "./EvidenceChip";

const KIND_TEXT: Record<string, string> = {
  lesson: "教训",
  decision: "已定口径",
  rejected: "已否掉",
};

const NO_MEMORY =
  "这个项目还没有确认过的记忆。草案卡上出现「这一轮攒下的记忆」时，勾了才会记下来。";

export function MemoryDrawer({
  memory,
  pending,
  onDelete,
  onCollapse,
  projectId,
}: {
  memory: MemoryEntryView[];
  pending: boolean;
  onDelete: (memoryId: string) => void;
  onCollapse: () => void;
  /** 传了才打「久未使用」标：抽屉打开时懒查 usage；不传 / 查失败 = 无标（静默降级）。 */
  projectId?: string;
}) {
  const [unused, setUnused] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    if (projectId === undefined) return;
    let alive = true;
    api.memoryUsage(projectId).then(
      (view) => {
        if (alive) setUnused(new Set(view.unused));
      },
      () => {
        /* 工具/网络类失败不判死：无标就是今天的抽屉，不打断。 */
      },
    );
    return () => {
      alive = false;
    };
  }, [projectId]);

  const groups = new Map<string, MemoryEntryView[]>();
  for (const entry of memory) {
    groups.set(entry.kind, [...(groups.get(entry.kind) ?? []), entry]);
  }

  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="memory-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="memory-title">
          记忆 · {memory.length} 条
        </h2>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCollapse}>
          收起
        </button>
      </header>

      <div className="drawer__body">
        <p className="input__hint">
          这些是你逐条确认过、会影响以后每一轮判断的东西。删掉一条，下一轮就不再看到它。
        </p>
        {memory.length === 0 && <p className="muted small">{NO_MEMORY}</p>}
        {[...groups.entries()].map(([kind, entries]) => (
          <section className="loop__group" key={kind}>
            <p className="loop__label">{KIND_TEXT[kind] ?? kind}</p>
            {entries.map((entry) => (
              <article className="prop" key={entry.memory_id}>
                <div>
                  <p className="prop__text">{entry.text}</p>
                  <div className="prop__foot">
                    <span className="tag">{entry.memory_id}</span>
                    {unused.has(entry.memory_id) && <span className="tag">久未使用</span>}
                    <EvidenceChip eids={entry.refs} />
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      disabled={pending}
                      onClick={() => onDelete(entry.memory_id)}
                    >
                      删掉这条
                    </button>
                  </div>
                </div>
              </article>
            ))}
          </section>
        ))}
      </div>
    </aside>
  );
}
