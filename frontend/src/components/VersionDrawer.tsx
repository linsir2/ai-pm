/* 版本历史抽屉：列表 + 选两个版本看差异 + 回到某一版。
   与问卷/草案抽屉同一条规矩——只盖闭环区，文档始终看得见；
   回退是"产生新的一版"，不是删历史（`01` F3 / `04` D6）。 */

import { useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { DiffView, VersionSummaryView } from "../api/types";
import { diffHeadline, pickPair, versionLabel } from "../document/versions";

function describeError(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error);
}

export function VersionDrawer({
  projectId,
  current,
  onRollback,
  onCollapse,
}: {
  projectId: string;
  current: number;
  onRollback: (versionNo: number) => Promise<void>;
  onCollapse: () => void;
}) {
  const [versions, setVersions] = useState<VersionSummaryView[]>([]);
  const [pair, setPair] = useState<[number, number] | null>(null);
  const [diff, setDiff] = useState<DiffView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [arming, setArming] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api
      .versions(projectId)
      .then((list) => {
        setVersions(list.versions);
        // 默认比"上一版 → 当前版"：最常看的那个问题
        const previous = list.versions[list.versions.length - 2]?.version_no;
        setPair(previous === undefined ? [list.current, list.current] : [previous, list.current]);
      })
      .catch((cause: unknown) => setError(describeError(cause)));
  }, [projectId]);

  useEffect(() => {
    if (pair === null) return;
    void api
      .versionDiff(projectId, pair[0], pair[1])
      .then(setDiff)
      .catch((cause: unknown) => setError(describeError(cause)));
  }, [pair, projectId]);

  return (
    <aside className="drawer" role="dialog" aria-modal="false" aria-labelledby="version-title">
      <header className="drawer__head">
        <h2 className="drawer__title" id="version-title">
          版本历史 · {versions.length} 版
        </h2>
        <span className="topbar__spacer" />
        <button type="button" className="btn btn--ghost btn--sm" onClick={onCollapse}>
          收起
        </button>
      </header>

      <div className="drawer__body">
        {error !== null && <p className="drawer__note">{error}</p>}

        <ul className="versions">
          {versions.map((version) => (
            <li className="version" key={version.version_no}>
              <button
                type="button"
                className={
                  pair?.includes(version.version_no)
                    ? "version__pick version__pick--on"
                    : "version__pick"
                }
                onClick={() => setPair(pickPair(pair, version.version_no, current))}
              >
                <span className="mono">{versionLabel(version)}</span>
                <span className="muted small">{version.summary}</span>
              </button>
              {version.version_no !== current && (
                <button
                  type="button"
                  className="btn btn--sm btn--ghost"
                  disabled={busy}
                  title="回到这一版（会产生新的一版，历史不会删）"
                  onClick={() => setArming(version.version_no)}
                >
                  回到这一版
                </button>
              )}
              {arming === version.version_no && (
                <span className="version__confirm">
                  回到 v{version.version_no}？这会新增一版，历史一版都不删。
                  <button
                    type="button"
                    className="btn btn--sm btn--primary"
                    onClick={() => {
                      setBusy(true);
                      void onRollback(version.version_no).finally(() => {
                        setBusy(false);
                        setArming(null);
                      });
                    }}
                  >
                    确认回退
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>

        {diff !== null && (
          <section className="diff" aria-label="版本差异">
            <h3 className="diff__title">{diffHeadline(diff)}</h3>
            {diff.groups.map((group) => (
              <div className="diff__group" key={group.target_label}>
                <h4 className="diff__label">{group.target_label}</h4>
                {group.added.map((item) => (
                  <p className="diff__line diff__line--add" key={`a-${item.entry_id}`}>
                    + {item.text}〔{item.refs.join(" ")}〕
                  </p>
                ))}
                {group.removed.map((item) => (
                  <p className="diff__line diff__line--del" key={`d-${item.entry_id}`}>
                    − {item.text}〔{item.refs.join(" ")}〕
                  </p>
                ))}
                {group.changed.map((item) => (
                  <div className="diff__change" key={`c-${item.after.entry_id}`}>
                    <p className="diff__line diff__line--del">
                      − {item.before.text}〔{item.before.refs.join(" ")}〕
                    </p>
                    <p className="diff__line diff__line--add">
                      + {item.after.text}〔{item.after.refs.join(" ")}〕
                    </p>
                  </div>
                ))}
              </div>
            ))}
          </section>
        )}
      </div>
    </aside>
  );
}
