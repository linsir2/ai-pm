/* 管理后台（Task 8）：审批队列 / 用户列表（含用量）/ 封禁。
   权限判定只在这里读 `useAuth()` 的 role——非 admin 渲染"无权限"，一个管理端点都不打。
   数据全从后端读回来（`/auth/users` + `/auth/usage`）。

   **判断都在 `state/admin.ts` 的纯函数里**（前端不变式：组件不做业务判断，`06` §3.2 第 5 条）：
   谁在待批、该封禁还是解封、用量缺省几，组件一个都不自己算，只调用取形状、渲染。 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { AuthUser, UsageRow } from "../api/client";
import { actionFor, pendingUsers, usageOf } from "../state/admin";
import type { AdminActionKind } from "../state/admin";
import { useAuth } from "../state/auth";

/** 动作 → 端点 / 样式：形状驱动的一张表，不是分支判断。解封复用 approve（无独立 unban 端点）。 */
const ACTION_CALL: Record<AdminActionKind, (userId: string) => Promise<AuthUser>> = {
  ban: api.ban,
  unban: api.approve,
};
const ACTION_CLASS: Record<AdminActionKind, string> = {
  ban: "btn btn--sm btn--ghost",
  unban: "btn btn--sm",
};

export function AdminView() {
  const { user, ready } = useAuth();

  if (!ready) {
    return (
      <main className="doc" aria-label="管理后台">
        <div className="doc__inner">
          <p className="muted">正在读取身份…</p>
        </div>
      </main>
    );
  }
  if (user === null || user.role !== "admin") {
    return (
      <main className="doc" aria-label="管理后台">
        <div className="doc__inner">
          <p role="alert" className="banner banner--error">
            无权限：只有管理员能进管理后台。
          </p>
        </div>
      </main>
    );
  }
  return <AdminPanel />;
}

const rowStyle = { display: "flex", gap: "var(--space-2)", alignItems: "center" } as const;

function AdminPanel() {
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [usage, setUsage] = useState<UsageRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const [listing, counts] = await Promise.all([api.users(), api.usage()]);
      setUsers(listing.users);
      setUsage(counts.usage);
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const act = useCallback(
    (run: () => Promise<unknown>) => {
      setBusy(true);
      void run()
        .then(() => reload())
        .catch((caught) =>
          setError(caught instanceof ApiError ? caught.message : "操作失败"),
        )
        .finally(() => setBusy(false));
    },
    [reload],
  );

  const waiting = pendingUsers(users);

  return (
    <main className="doc" aria-label="管理后台">
      <div className="doc__inner">
        <h1>管理后台</h1>

        {error !== null && (
          <p role="alert" className="banner banner--error">
            {error}
          </p>
        )}

        {loading ? (
          <p className="muted">正在读取用户…</p>
        ) : (
          <>
            <section className="pane-card" aria-label="审批队列">
              <h2 className="muted small">审批队列（{waiting.length}）</h2>
              {waiting.length === 0 ? (
                <p className="muted small">没有待审批的用户。</p>
              ) : (
                <ul className="stack" style={{ listStyle: "none", padding: 0 }}>
                  {waiting.map((entry) => (
                    <li key={entry.user_id} style={rowStyle}>
                      <span>{entry.username}</span>
                      <button
                        type="button"
                        className="btn btn--sm"
                        disabled={busy}
                        onClick={() => act(() => api.approve(entry.user_id))}
                      >
                        批准
                      </button>
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        disabled={busy}
                        onClick={() => act(() => api.reject(entry.user_id))}
                      >
                        拒绝
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section className="pane-card" aria-label="用户列表">
              <h2 className="muted small">用户（{users.length}）</h2>
              <ul className="stack" style={{ listStyle: "none", padding: 0 }}>
                {users.map((entry) => {
                  const action = actionFor(entry);
                  return (
                    <li key={entry.user_id} style={rowStyle}>
                      <span>{entry.username}</span>
                      <span className="muted small">
                        {entry.role} · {entry.status} · 用量{" "}
                        {usageOf(entry.user_id, usage)}
                      </span>
                      <button
                        type="button"
                        className={ACTION_CLASS[action.kind]}
                        disabled={busy}
                        onClick={() => act(() => ACTION_CALL[action.kind](entry.user_id))}
                      >
                        {action.label}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
