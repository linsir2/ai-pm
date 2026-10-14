/* 发起调研的抽屉（M8）：锚点说清"调研什么"，一次只发起一趟。
   它是发起卡，不是结果——结果在闭环区的「过目台」上逐条过目。 */

import type { ExplorationAnchor } from "../api/payload";
import { truncateFocus } from "../api/payload";

export function ResearchDrawer({
  anchor,
  pending,
  onStart,
  onCancel,
}: {
  anchor: ExplorationAnchor;
  pending: boolean;
  onStart: () => void;
  onCancel: () => void;
}) {
  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="research-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="research-title">
          发起调研
        </h2>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
          取消
        </button>
      </header>

      <div className="drawer__body">
        <p className="input__hint">
          调研只出一份"待过目的候选"（假设与盲区），不会直接改文档；收下的才会回流。
        </p>
        <section className="loop__group">
          <p className="loop__label">调研什么</p>
          {anchor.kind === "entry" ? (
            <article className="prop">
              <div>
                <p className="prop__text">{anchor.focus}</p>
                {truncateFocus(anchor.focus) !== anchor.focus && (
                  <p className="muted small">
                    （选段过长，仅前 500 字作补充说明；完整条目已由编号带入）
                  </p>
                )}
                <div className="prop__foot">
                  <span className="tag">{anchor.ref}</span>
                </div>
              </div>
            </article>
          ) : (
            <p className="muted small">
              整份文档：通读现有文档，找还没被想透的假设与没被看到的盲区。
            </p>
          )}
        </section>
      </div>

      <div className="drawer__foot">
        <button type="button" className="btn btn--primary" disabled={pending} onClick={onStart}>
          {pending ? "正在调研…" : "开始调研"}
        </button>
        <span className="input__hint">
          同项目同时只允许一趟；跑完到闭环区的「过目台」逐条过目。
        </span>
      </div>
    </aside>
  );
}
