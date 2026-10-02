/* 就绪度清单（`06-前端.md` §2.2 承诺过、P29 眼睛检查 F6 才补上的那一块）。

   为什么要有：底栏那句"就差 N 项"以前点不动——用户知道差几项，却不知道差在哪一条。
   现在点它开这个抽屉：**按章分组**，每条写 title + detail + 阻塞/提示标签。

   判据的真相源在后端 `domain/authoring/readiness.py`，这里只归拢与显示。 */

import type { DocumentView, ReadinessItemView } from "../api/types";
import { readinessBySection, readinessSummary } from "../document/readiness";

export function ReadinessDrawer({
  items,
  document: doc,
  onCollapse,
}: {
  items: readonly ReadinessItemView[];
  document: DocumentView | null;
  onCollapse: () => void;
}) {
  const summary = readinessSummary(items);
  const groups = readinessBySection(items, doc?.sections ?? []);

  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="readiness-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="readiness-title">
          就绪度 · 阻塞 {summary.blockers} 项 / 提示 {summary.warns} 项
        </h2>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCollapse}>
          收起
        </button>
      </header>

      <div className="drawer__body">
        <p className="muted small">
          阻塞项决定"能不能交出去"（`ready = 阻塞 == 0`）；提示项只是提醒。
          **补缺口不等于填满**：拿不准就把它留成开放问题。
        </p>

        {groups.length === 0 ? (
          <p className="muted small">没有缺口：这份文档按判据已经就绪。</p>
        ) : (
          groups.map((group) => (
            <section className="loop__group" key={group.sectionId}>
              <p className="loop__label">
                {group.sectionId} {group.title}
                {group.blockers > 0 && ` · 阻塞 ${group.blockers}`}
              </p>
              <ul className="entries">
                {group.items.map((item) => (
                  <li className="entry" key={`${item.section_id}-${item.code}`}>
                    {/* `.entry` 是"正文在左、标签在右"的两列栅格：顺序反了会叠在一起 */}
                    <span className="entry__text">
                      {item.title}
                      {item.detail !== "" && <span className="muted small">（{item.detail}）</span>}
                    </span>
                    <span
                      className={`tag tag--${item.severity === "blocker" ? "conflict" : "open"}`}
                    >
                      {item.severity === "blocker" ? "阻塞" : "提示"}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </div>
    </aside>
  );
}
