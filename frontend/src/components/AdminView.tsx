/* 管理后台（Task 8）：审批队列 / 用户列表（含用量）/ 封禁。
   权限判定只在这里读 `useAuth()` 的 role——非 admin 渲染"无权限"，一个管理端点都不打。
   数据全从后端读回来（`/auth/users` + `/auth/usage`），组件不拼数、不编数。

   解封：后端没有独立的 unban 端点，`POST /auth/approve` 对任意状态的用户置 active（用例
   不做状态机限制），所以解封复用它——真实生效，不是假实现。若后端日后给 approve 加状态
   门，这里要跟着改（见 Task 8 报告疑虑）。 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { AuthUser, UsageRow } from "../api/client";
import { useAuth } from "../state/auth";

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

  const callsById = new Map(usage.map((row) => [row.user_id, row.calls]));
  const waiting = users.filter((entry) => entry.status === "pending");

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
                {users.map((entry) => (
                  <li key={entry.user_id} style={rowStyle}>
                    <span>{entry.username}</span>
                    <span className="muted small">
                      {entry.role} · {entry.status} · 用量 {callsById.get(entry.user_id) ?? 0}
                    </span>
                    {entry.status === "banned" ? (
                      <button
                        type="button"
                        className="btn btn--sm"
                        disabled={busy}
                        onClick={() => act(() => api.approve(entry.user_id))}
                      >
                        解封
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--sm btn--ghost"
                        disabled={busy}
                        onClick={() => act(() => api.ban(entry.user_id))}
                      >
                        封禁
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
