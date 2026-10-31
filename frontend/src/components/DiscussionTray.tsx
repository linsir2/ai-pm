/* 讨论区（M9）：右栏第三个页签。
   讨论是挂 project 的自由主题多轮聊天：一说就记一对，回合不挂在任何一轮上。
   判断全在 `state/discussions.ts` 的纯函数里（能不能追问、能不能收卡、默认请谁），
   组件只把结果画出来、把动作递出去。 */

import { useEffect, useRef, useState } from "react";

import type { ExplorationView, ThreadView } from "../api/types";
import { REVIEW_ACTION_TEXT } from "../labels";
import {
  authorTitle,
  canAssemble,
  canDiscuss,
  cardActions,
  cardStatusText,
  defaultPanelRoles,
  joinableRoles,
  roleChoices,
  roundsOf,
  threadStatusText,
  turnsLeft,
  validatePanel,
  PANEL_ROUNDS_DEFAULT,
  PANEL_ROUNDS_MAX,
  PANEL_ROUNDS_MIN,
} from "../state/discussions";
import type { DiscussionAnchor, DiscussionRoleOption, PanelStart } from "../state/discussions";
import { isRunning } from "../state/explorations";
import { EvidenceChip } from "./EvidenceChip";

export function DiscussionTray({
  threads,
  /** 开聊卡上的角色选项：由 `roleOptions(schema)` 派生（真相源在后端 roles.toml）。 */
  roles: roleOptionsProp,
  /** 线程 turn 上限（`/schema.limits.max_discussion_turns` 或离线回落，M13）。 */
  turnLimit,
  anchor = null,
  pending,
  busy = false,
  busyText = "正在处理…",
  onStartPanel,
  onRunRound,
  onJoin,
  onAbortRun,
  onSay,
  onCards,
  onKeep,
  onDismiss,
  onVerify,
  onClose,
  onStartResearch,
  explorations = [],
  explorationsPending = false,
}: {
  threads: ThreadView[];
  /** 角色选项（`/schema` 的 `discussion_roles`，或离线回落那一条）。 */
  roles: DiscussionRoleOption[];
  /** 线程 turn 上限（LoopColumn 从 `schema?.limits` 算好传进来；不在这里再抄一份）。 */
  turnLimit: number;
  /** 问卷里点「聊聊」带过来的题面与问的人（讨论 tab 自由开聊时为 null）。 */
  anchor?: DiscussionAnchor | null;
  /** 有讨论动作在途（开聊 / 说 / 总结 / 收卡 / 关掉）：按钮先禁掉，防连点。 */
  pending: boolean;
  /** 有任何动作在途（全局串行闸）：开跑按钮也禁掉，并写明正在做什么。 */
  busy?: boolean;
  /** busy 时的界面说法（由 `pendingActionLabel` 派生，组件不自己拼文案）。 */
  busyText?: string;
  /** 开跑一个 panel（M18）：建线程后调用方自动循环跑 N 轮；返回线程 id（失败 null）。 */
  onStartPanel: (start: PanelStart, runInquiry: boolean) => Promise<string | null>;
  /** 跑一轮（M18）：panel 内并行发言 + 综合；返回最新线程（失败/被停 null）。 */
  onRunRound: (threadId: string, signal?: AbortSignal) => Promise<ThreadView | null>;
  /** 中途拉人（M18）：下一轮生效。 */
  onJoin: (threadId: string, roleId: string) => void;
  /** 停掉在跑的一轮（M18）：服务端也停，不写状态。 */
  onAbortRun: (threadId: string) => void;
  onSay: (threadId: string, text: string) => void;
  onCards: (threadId: string) => void;
  onKeep: (threadId: string, cardId: string, as: "seed" | "material") => void;
  onDismiss: (threadId: string, cardId: string) => void;
  /** 拿去验证（M13）：拿卡片原话起一趟聚焦调研；候选进灵感箱。 */
  onVerify: (statement: string) => void;
  onClose: (threadId: string) => void;
  /** 整份文档调研的发起（M17：从灵感箱底部搬过来，与"讨论/调研一体"）。
      不传就不出这一行。 */
  onStartResearch?: () => void;
  /** 按钮忙闲的依据（与灵感箱同一口径：动作在途或有趟在跑就禁用）。 */
  explorations?: ExplorationView[];
  explorationsPending?: boolean;
}) {
  const anchorTopic = anchor?.topic ?? "";
  const anchorRole = anchor?.role_id ?? null;
  const [topic, setTopic] = useState(anchorTopic);
  const [roleIds, setRoleIds] = useState<string[]>(() => defaultPanelRoles(anchorRole, roleOptionsProp));
  const [rounds, setRounds] = useState(PANEL_ROUNDS_DEFAULT);
  // 锚点变了就重新预填：从问卷点「聊聊」时抽屉还开着，托盘可能已经挂载过一次。
  useEffect(() => {
    setTopic(anchorTopic);
    setRoleIds(defaultPanelRoles(anchorRole, roleOptionsProp));
  }, [anchorTopic, anchorRole]);
  // 追问的话按线程分别存（一条线程的草稿不该串到另一条上）。
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // 拉人下拉框按线程分别存。
  const [joinPicks, setJoinPicks] = useState<Record<string, string>>({});
  // 跑动中的线程：{want: 还要跑到几轮}。N 由前端循环驱动，后端只管一轮。
  const [runs, setRuns] = useState<Record<string, { want: number }>>({});
  const stopRef = useRef<Record<string, boolean>>({});
  const aborters = useRef<Record<string, AbortController>>({});
  // 「先跑一轮调研」（M13）：勾了就先起一趟聚焦调研，假设当开题底稿带进讨论
  const [runInquiry, setRunInquiry] = useState(false);

  const roles = roleChoices(anchorRole, roleOptionsProp);
  const trimmed = topic.trim();
  const panelProblem = validatePanel(roleIds, rounds);
  const canStart = trimmed !== "" && panelProblem === null;
  const researchBusy = explorationsPending || isRunning(explorations);

  const isStopped = (threadId: string): boolean => stopRef.current[threadId] === true;

  const runLoop = async (threadId: string, want: number): Promise<void> => {
    setRuns((current) => ({ ...current, [threadId]: { want } }));
    // stopRef 的键在每轮循环收尾时删掉，重新跑自然是干净的。
    // 读它一律走 `isStopped()`：直接索引比较会被 TS 的窄化判成"不可能"。
    try {
      for (;;) {
        if (isStopped(threadId)) break;
        const controller = new AbortController();
        aborters.current[threadId] = controller;
        let view: ThreadView | null;
        try {
          view = await onRunRound(threadId, controller.signal);
        } finally {
          delete aborters.current[threadId];
        }
        if (isStopped(threadId)) break;
        if (view === null) break;
        if (view.summaries.length >= want) break;
      }
    } finally {
      setRuns((current) => {
        const next = { ...current };
        delete next[threadId];
        return next;
      });
      delete stopRef.current[threadId];
    }
  };

  const startAndRun = async (): Promise<void> => {
    if (busy || !canStart) return;
    const threadId = await onStartPanel(
      { topic: trimmed, role_ids: roleIds, question: anchor?.question ?? null },
      runInquiry,
    );
    if (threadId !== null) void runLoop(threadId, rounds);
  };

  const stopRun = (threadId: string): void => {
    stopRef.current[threadId] = true;
    aborters.current[threadId]?.abort();
    onAbortRun(threadId);
  };

  return (
    <div className="loop__scroll">
      <div className="loop__head">
        <h2 className="loop__title">讨论区</h2>
      </div>
      <p className="muted small">
        讨论不写文档；聊出想法「总结成卡」，收下才回流下一轮。
      </p>

      {onStartResearch !== undefined && (
        <section className="loop__group">
          <p className="loop__label">发起调研</p>
          <p className="muted small">整份文档还没想透的，先跑一趟调研再聊；候选回灵感箱过目。</p>
          <div className="loop__actions">
            <button
              type="button"
              className="btn btn--sm"
              disabled={researchBusy}
              onClick={onStartResearch}
            >
              发起调研（整份文档）
            </button>
            {isRunning(explorations) && (
              <span className="input__hint">同一项目同时只允许一趟调研。</span>
            )}
          </div>
        </section>
      )}

      <section className="loop__group">
        <p className="loop__label">开一个讨论 panel</p>
        <label className="sr-only" htmlFor="discussion-topic">
          讨论主题
        </label>
        <textarea
          id="discussion-topic"
          className="q__text"
          value={topic}
          placeholder="想跟谁聊什么？（自由主题，不限文档里有没有这条）"
          onChange={(event) => setTopic(event.target.value)}
        />
        <fieldset className="seats seats--compact">
          <legend className="loop__label">请谁（1–4 个，到齐就开跑）</legend>
          {roles.map((role) => {
            const on = roleIds.includes(role.role_id);
            return (
              <label
                className={on ? "seat seat--on" : "seat"}
                key={role.role_id}
                title={role.focus !== "" ? `它管什么：${role.focus}` : undefined}
              >
                <input
                  type="checkbox"
                  name="discussion-roles"
                  value={role.role_id}
                  checked={on}
                  onChange={() =>
                    setRoleIds((ids) =>
                      on ? ids.filter((id) => id !== role.role_id) : [...ids, role.role_id],
                    )
                  }
                />
                <span>
                  <span className="seat__title">{role.title}</span>
                  {role.focus !== "" && <span className="seat__focus">{role.focus}</span>}
                </span>
              </label>
            );
          })}
          <label className={runInquiry ? "seat seat--on" : "seat"}>
            <input
              type="checkbox"
              checked={runInquiry}
              onChange={() => setRunInquiry((on) => !on)}
            />
            <span>
              <span className="seat__title">先跑一轮调研</span>
              <span className="seat__focus">
                先出一趟假设与盲区（进灵感箱过目），当这次讨论的开题底稿
              </span>
            </span>
          </label>
        </fieldset>
        <div className="prop__foot">
          <label className="muted small" htmlFor="discussion-rounds">
            跑几轮
          </label>
          <select
            id="discussion-rounds"
            className="select"
            value={rounds}
            onChange={(event) => setRounds(Number(event.target.value))}
          >
            {Array.from(
              { length: PANEL_ROUNDS_MAX - PANEL_ROUNDS_MIN + 1 },
              (_, i) => PANEL_ROUNDS_MIN + i,
            ).map((n) => (
              <option key={n} value={n}>
                {n} 轮
              </option>
            ))}
          </select>
        </div>
        <div className="prop__foot">
          <button
            type="button"
            className="btn"
            disabled={pending || busy || !canStart}
            onClick={() => void startAndRun()}
          >
            开跑
          </button>
          {busy ? (
            <span className="input__hint">{busyText}</span>
          ) : (
            panelProblem !== null && <span className="input__hint">{panelProblem}</span>
          )}
        </div>
      </section>

      {threads.length === 0 && (
        <p className="muted small">还没开过讨论——上面写一句就能开一条。</p>
      )}

      {threads.map((thread) => {
        const draft = drafts[thread.thread_id] ?? "";
        const open = thread.status === "open";
        const running = runs[thread.thread_id] !== undefined;
        const runWant = runs[thread.thread_id]?.want ?? 0;
        const doneRounds = thread.summaries.length;
        const paneled =
          thread.summaries.length > 0 ||
          thread.participants.length > 1 ||
          new Set(thread.turns.map((turn) => turn.round)).size > 1;
        const joinable = joinableRoles(roles, thread);
        const joinPick = joinPicks[thread.thread_id] ?? joinable[0]?.role_id ?? "";
        return (
          <section className="loop__group" key={thread.thread_id}>
            <p className="loop__label">{thread.topic}</p>
            <div className="prop__foot">
              <span className="tag">{thread.role_title}</span>
              <span className="muted small">{threadStatusText(thread.status)}</span>
              {thread.question !== null && (
                <span className="muted small">从问卷来：{thread.question.title}</span>
              )}
              {thread.findings_count > 0 && (
                <span className="muted small">开题调研底稿 {thread.findings_count} 条</span>
              )}
            </div>
            {thread.participants.length > 1 && (
              <p className="muted small">
                在场：
                {thread.participants
                  .map((member) =>
                    member.joined_round > 1
                      ? `${member.role_title}（第${member.joined_round}轮加入）`
                      : member.role_title,
                  )
                  .join(" · ")}
              </p>
            )}
            {running && (
              <div className="prop__foot">
                <span className="muted small">
                  正在跑第 {doneRounds + 1}/{runWant} 轮…
                </span>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  onClick={() => stopRun(thread.thread_id)}
                >
                  停止
                </button>
              </div>
            )}

            {(thread.turns.length > 0 || thread.summaries.length > 0) &&
              (!paneled ? (
                <div className="prop__rationale" role="log" aria-label={thread.topic}>
                  {thread.turns.map((turn) => (
                    <p key={turn.index}>
                      <span className="tag">
                        {turn.role === "user" ? "你" : thread.role_title}
                      </span>{" "}
                      {turn.text}
                    </p>
                  ))}
                </div>
              ) : (
                <div className="prop__rationale" role="log" aria-label={thread.topic}>
                  {roundsOf(thread).map(({ round, turns }) => {
                    const summary = thread.summaries.find((item) => item.index === round);
                    return (
                      <div key={round}>
                        <p className="muted small">{`第 ${round} 轮`}</p>
                        {turns.map((turn) => (
                          <p key={turn.index}>
                            <span className="tag">
                              {turn.role === "user" ? "你" : authorTitle(thread, turn.author)}
                            </span>{" "}
                            {turn.text}
                          </p>
                        ))}
                        {summary !== undefined && (
                          <div className="summary">
                            {summary.agreed.length > 0 && (
                              <p className="muted small">{`共识：${summary.agreed.join("；")}`}</p>
                            )}
                            {summary.disagreed.length > 0 && (
                              <p className="muted small">{`分歧：${summary.disagreed.join("；")}`}</p>
                            )}
                            {summary.open.length > 0 && (
                              <p className="muted small">{`未决：${summary.open.join("；")}`}</p>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              ))}

            {canDiscuss(thread, turnLimit) ? (
              <>
                <label className="sr-only" htmlFor={`say-${thread.thread_id}`}>
                  说这一轮
                </label>
                <textarea
                  id={`say-${thread.thread_id}`}
                  className="q__text"
                  value={draft}
                  placeholder={`跟${thread.role_title}再说一句（还能说 ${turnsLeft(thread, turnLimit)} 轮）`}
                  onChange={(event) =>
                    setDrafts((all) => ({ ...all, [thread.thread_id]: event.target.value }))
                  }
                />
                <div className="prop__foot">
                  <button
                    type="button"
                    className="btn btn--sm"
                    disabled={pending || draft.trim() === ""}
                    onClick={() => onSay(thread.thread_id, draft.trim())}
                  >
                    说这一轮
                  </button>
                </div>
              </>
            ) : (
              open && (
                <p className="prop__warn">
                  已经聊满（上限 {turnLimit} 条，一说是一对）：可以「总结成卡」，也可以关掉它。
                </p>
              )
            )}

            <div className="prop__foot">
              <button
                type="button"
                className="btn btn--sm"
                disabled={pending || !canAssemble(thread)}
                onClick={() => onCards(thread.thread_id)}
              >
                总结成卡
              </button>
              {open && !running && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={pending}
                  onClick={() => void runLoop(thread.thread_id, thread.summaries.length + 1)}
                  title="接着往下再跑一轮"
                >
                  再跑一轮
                </button>
              )}
              {open && (
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={pending}
                  onClick={() => onClose(thread.thread_id)}
                >
                  关掉这条
                </button>
              )}
            </div>
            {open && joinable.length > 0 && (
              <div className="prop__foot">
                <label className="muted small" htmlFor={`join-${thread.thread_id}`}>
                  拉人进来（下一轮生效）
                </label>
                <select
                  id={`join-${thread.thread_id}`}
                  className="select"
                  value={joinPick}
                  onChange={(event) =>
                    setJoinPicks((all) => ({ ...all, [thread.thread_id]: event.target.value }))
                  }
                >
                  {joinable.map((role) => (
                    <option key={role.role_id} value={role.role_id}>
                      {role.title}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={pending || joinPick === ""}
                  onClick={() => onJoin(thread.thread_id, joinPick)}
                >
                  拉进来
                </button>
              </div>
            )}

            {thread.cards.length > 0 && (
              <div className="prop__rationale">
                {thread.cards.map((card) => (
                  <article className="prop" key={card.card_id}>
                    <div>
                      <p className="prop__text">{card.statement}</p>
                      <div className="prop__foot">
                        <span className="tag">
                          {card.grounding === "document" ? "文档里有据" : "凭空推演"}
                        </span>
                        <EvidenceChip eids={card.based_on} />
                      </div>
                      <div className="prop__foot">
                        {cardActions(card).map((action) => (
                          <button
                            key={action}
                            type="button"
                            className={
                              action === "dismiss" ? "btn btn--ghost btn--sm" : "btn btn--sm"
                            }
                            disabled={pending}
                            onClick={() => {
                              if (action === "dismiss") {
                                onDismiss(thread.thread_id, card.card_id);
                              } else {
                                onKeep(
                                  thread.thread_id,
                                  card.card_id,
                                  action === "keep-seed" ? "seed" : "material",
                                );
                              }
                            }}
                          >
                            {REVIEW_ACTION_TEXT[action]}
                          </button>
                        ))}
                        {cardActions(card).length > 0 && (
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            disabled={pending}
                            onClick={() => onVerify(card.statement)}
                          >
                            拿去验证
                          </button>
                        )}
                        {cardStatusText(card) !== null && (
                          <span className="muted small">{cardStatusText(card)}</span>
                        )}
                      </div>
                    </div>
                  </article>
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
