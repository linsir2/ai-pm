/* 讨论区（M9）：右栏第三个页签。
   讨论是挂 project 的自由主题多轮聊天：一说就记一对，回合不挂在任何一轮上。
   判断全在 `state/discussions.ts` 的纯函数里（能不能追问、能不能收卡、默认请谁），
   组件只把结果画出来、把动作递出去。 */

import { useEffect, useState } from "react";

import type { ThreadView } from "../api/types";
import { REVIEW_ACTION_TEXT } from "../labels";
import {
  canAssemble,
  canDiscuss,
  cardActions,
  cardStatusText,
  resolveRoleId,
  roleChoices,
  threadStatusText,
  turnsLeft,
} from "../state/discussions";
import type { DiscussionAnchor, DiscussionRoleOption } from "../state/discussions";
import { EvidenceChip } from "./EvidenceChip";

export function DiscussionTray({
  threads,
  /** 开聊卡上的角色选项：由 `roleOptions(schema)` 派生（真相源在后端 roles.toml）。 */
  roles: roleOptionsProp,
  /** 线程 turn 上限（`/schema.limits.max_discussion_turns` 或离线回落，M13）。 */
  turnLimit,
  anchor = null,
  pending,
  onStart,
  onSay,
  onCards,
  onKeep,
  onDismiss,
  onVerify,
  onClose,
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
  onStart: (anchor: { topic: string; role_id: string }, runInquiry: boolean) => void;
  onSay: (threadId: string, text: string) => void;
  onCards: (threadId: string) => void;
  onKeep: (threadId: string, cardId: string, as: "seed" | "material") => void;
  onDismiss: (threadId: string, cardId: string) => void;
  /** 拿去验证（M13）：拿卡片原话起一趟聚焦调研；候选进灵感箱。 */
  onVerify: (statement: string) => void;
  onClose: (threadId: string) => void;
}) {
  const anchorTopic = anchor?.topic ?? "";
  const anchorRole = anchor?.role_id ?? null;
  const [topic, setTopic] = useState(anchorTopic);
  const [roleId, setRoleId] = useState(() => resolveRoleId(anchorRole));
  // 锚点变了就重新预填：从问卷点「聊聊」时抽屉还开着，托盘可能已经挂载过一次。
  useEffect(() => {
    setTopic(anchorTopic);
    setRoleId(resolveRoleId(anchorRole));
  }, [anchorTopic, anchorRole]);
  // 追问的话按线程分别存（一条线程的草稿不该串到另一条上）。
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  // 「先跑一轮调研」（M13）：勾了就先起一趟聚焦调研，假设当开题底稿带进讨论
  const [runInquiry, setRunInquiry] = useState(false);

  const roles = roleChoices(anchorRole, roleOptionsProp);
  const trimmed = topic.trim();

  return (
    <div className="loop__scroll">
      <div className="loop__head">
        <h2 className="loop__title">讨论区</h2>
      </div>
      <p className="muted small">
        讨论不写文档；聊出想法「总结成卡」，收下才回流下一轮。
      </p>

      <section className="loop__group">
        <p className="loop__label">开一条讨论</p>
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
          <legend className="loop__label">请谁</legend>
          {roles.map((role) => (
            <label
              className={roleId === role.role_id ? "seat seat--on" : "seat"}
              key={role.role_id}
              title={role.focus !== "" ? `它管什么：${role.focus}` : undefined}
            >
              <input
                type="radio"
                name="discussion-role"
                value={role.role_id}
                checked={roleId === role.role_id}
                onChange={() => setRoleId(role.role_id)}
              />
              <span>
                <span className="seat__title">{role.title}</span>
                {role.focus !== "" && <span className="seat__focus">{role.focus}</span>}
              </span>
            </label>
          ))}
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
          <button
            type="button"
            className="btn"
            disabled={pending || trimmed === ""}
            onClick={() => onStart({ topic: trimmed, role_id: resolveRoleId(roleId) }, runInquiry)}
          >
            开聊
          </button>
        </div>
      </section>

      {threads.length === 0 && (
        <p className="muted small">还没开过讨论——上面写一句就能开一条。</p>
      )}

      {threads.map((thread) => {
        const draft = drafts[thread.thread_id] ?? "";
        const open = thread.status === "open";
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

            {thread.turns.length > 0 && (
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
            )}

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
