/* 管理后台（`06` §2.4）：概览 + **按状态分区**的用户表。
 *
 * 权限判定只在这里读 `useAuth()` 的 role——非 admin 渲染"无权限"，一个管理端点都不打。
 * 数据从 `/auth/stats` 一条读回来（每用户一行：项目/轮次/调用/失败/耗时/token/会话/
 * 最近活跃/注册与审批）。**这一屏不再打 `/auth/users` 与 `/auth/usage`**：那两个窄口径
 * 的读已经不够用，而把两个请求的结果在前端 join 起来正是"两个真相源"——
 * 一次动作之后两边的时点还可能对不上。
 *
 * **判断都在 `state/admin.ts` 的纯函数里**（前端不变式：组件不做业务判断，`06` §3.2 第 5 条）：
 * 谁分到哪一区、这一行给哪些按钮、概览那几个数怎么算、毫秒怎么写、相对时间怎么写，
 * 组件一个都不自己算，只调用取形状、渲染。
 *
 * **不给死按钮**：admin 行一个按钮都不渲染（后端 `admin_protected` 硬拦，
 * 界面再给一个点了必然报错的按钮只会让人以为坏了）。 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "../api/client";
import type { AdminStatsRow, AuthUser } from "../api/client";
import {
  ADMIN_SECTIONS,
  actionsFor,
  approvalText,
  compactNumber,
  durationText,
  lastSeenText,
  overviewOf,
  sectionRows,
  showSection,
} from "../state/admin";
import type { AdminActionKind } from "../state/admin";
import { useAuth } from "../state/auth";

/** 动作 → 端点 / 样式：形状驱动的一张表，不是分支判断。恢复复用 approve（无独立 unban 端点）。 */
const ACTION_CALL: Record<Exclude<AdminActionKind, "none">, (userId: string) => Promise<AuthUser>> =
  {
    approve: api.approve,
    reject: api.reject,
    ban: api.ban,
  };
const ACTION_CLASS: Record<Exclude<AdminActionKind, "none">, string> = {
  approve: "btn btn--sm",
  reject: "btn btn--sm btn--ghost",
  ban: "btn btn--sm btn--ghost",
};

export function AdminView({ onHome }: { onHome?: () => void }) {
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
  return <AdminPanel onHome={onHome} />;
}

function AdminPanel({ onHome }: { onHome?: () => void }) {
  const [rows, setRows] = useState<AdminStatsRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const resp = await api.stats();
      setRows(resp.stats);
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
    (kind: Exclude<AdminActionKind, "none">, userId: string) => {
      setBusy(true);
      void ACTION_CALL[kind](userId)
        .then(() => reload())
        .catch((caught) =>
          setError(caught instanceof ApiError ? caught.message : "操作失败"),
        )
        .finally(() => setBusy(false));
    },
    [reload],
  );

  // 相对时间以**这次读回来的那一刻**为准，不在组件里 new Date（那样每次渲染都在动）。
  const now = new Date();
  const overview = overviewOf(rows);

  return (
    <main className="doc" aria-label="管理后台">
      <div className="doc__inner admin">
        <header className="adm__head">
          {onHome !== undefined && (
            <button type="button" className="home__back" onClick={onHome}>← 返回文档中心</button>
          )}
          <h1 className="home__title">管理后台</h1>
          <p className="home__lede">
            这一屏只管两件事：有没有人在等你放行，以及现在谁在用、用得怎么样。
            项目内容谁也看不到——每个账号只看得到自己的。
          </p>
        </header>

        {error !== null && (
          <p role="alert" className="banner banner--error">
            {error}
          </p>
        )}

        {loading ? (
          <p className="muted">正在读取用户…</p>
        ) : (
          <>
            <ul className="adm__facts" aria-label="总览">
              <Fact label="账号" value={String(overview.accounts)} />
              <Fact label="待审批" value={String(overview.awaiting)} tone={overview.awaiting > 0 ? "warn" : "plain"} />
              <Fact label="活跃会话" value={String(overview.liveSessions)} />
              <Fact label="模型调用" value={compactNumber(overview.calls)} />
              {/* 「失败」那一格：**模型与工具分开算**（各自的分母），同行带工具均延迟——
                  否则工具侧的失败率根本看不见（改前只有模型侧）。 */}
              <Fact
                label="失败"
                value={
                  `模型 ${overview.failed}（${(overview.failureRate * 100).toFixed(1)}%）` +
                  ` / 工具 ${overview.toolFailed}（${(overview.toolFailureRate * 100).toFixed(1)}%）` +
                  (overview.toolAvgDurationMs === null
                    ? ""
                    : ` · 工具均延迟 ${durationText(overview.toolAvgDurationMs)}`)
                }
                tone={
                  overview.failed > 0 || overview.toolFailed > 0
                    ? "danger"
                    : "plain"
                }
              />
              <Fact label="总 token" value={compactNumber(overview.tokens)} />
            </ul>

            {ADMIN_SECTIONS.filter((section) => showSection(rows, section)).map((section) => {
              const list = sectionRows(rows, section.id);
              return (
                <section className="adm__section" key={section.id} aria-label={section.title}>
                  <div className="adm__section-head">
                    <h2 className="adm__section-title">
                      {section.title}
                      <span className="adm__count">{list.length}</span>
                    </h2>
                    <p className="adm__section-note">{section.note}</p>
                  </div>

                  {list.length === 0 ? (
                    <p className="muted small adm__empty">这一区现在没人。</p>
                  ) : (
                    <div className="adm__table-wrap">
                      <table className="adm__table">
                        <thead>
                          <tr>
                            <th scope="col">账号</th>
                            <th scope="col">项目 / 轮次</th>
                            <th scope="col">调用</th>
                            <th scope="col">平均耗时</th>
                            <th scope="col">token</th>
                            <th scope="col">会话</th>
                            <th scope="col">最近活跃</th>
                            <th scope="col" className="adm__th-acts">
                              操作
                            </th>
                          </tr>
                        </thead>
                        <tbody>
                          {list.map((row) => (
                            <StatsRow
                              row={row}
                              now={now}
                              busy={busy}
                              onAct={act}
                              key={row.user_id}
                            />
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </section>
              );
            })}
          </>
        )}
      </div>
    </main>
  );
}

function Fact({
  label,
  value,
  tone = "plain",
}: {
  label: string;
  value: string;
  tone?: "plain" | "warn" | "danger";
}) {
  return (
    <li className={`adm__fact adm__fact--${tone}`}>
      <span className="adm__fact-label">{label}</span>
      <span className="adm__fact-value">{value}</span>
    </li>
  );
}

function StatsRow({
  row,
  now,
  busy,
  onAct,
}: {
  row: AdminStatsRow;
  now: Date;
  busy: boolean;
  onAct: (kind: Exclude<AdminActionKind, "none">, userId: string) => void;
}) {
  const actions = actionsFor(row).filter((action) => action.kind !== "none");
  const approval = approvalText(row);
  return (
    <tr>
      <td className="adm__td-who">
        <span className="adm__name">
          {row.username}
          {row.role === "admin" && <span className="tag">管理员</span>}
        </span>
        <span className="adm__sub">
          注册于 {row.created_at === null ? "—" : row.created_at.slice(0, 10)}
          {approval === null ? "" : ` · ${approval}`}
        </span>
      </td>
      <td className="adm__td-num">
        {row.projects} / {row.rounds}
      </td>
      <td className="adm__td-num">
        {compactNumber(row.calls)}
        {row.failed > 0 && <span className="adm__failed">失败 {row.failed}</span>}
      </td>
      <td className="adm__td-num" title={row.avg_duration_ms === null ? undefined : `${row.avg_duration_ms}ms`}>
        {durationText(row.avg_duration_ms)}
      </td>
      <td className="adm__td-num">{compactNumber(row.total_tokens)}</td>
      <td className="adm__td-num">{row.active_sessions}</td>
      <td className="adm__td-when" title={row.last_seen_at ?? "从未登录"}>
        {lastSeenText(row, now)}
      </td>
      <td className="adm__td-acts">
        {actions.length === 0 ? (
          /* 不给死按钮：不渲染，也不留空占位。管理员自己这行就是没有操作。 */
          <span className="muted small">—</span>
        ) : (
          actions.map((action) => (
            <button
              type="button"
              className={ACTION_CLASS[action.kind as Exclude<AdminActionKind, "none">]}
              disabled={busy}
              onClick={() =>
                onAct(action.kind as Exclude<AdminActionKind, "none">, row.user_id)
              }
              key={action.kind}
            >
              {action.label}
            </button>
          ))
        )}
      </td>
    </tr>
  );
}
