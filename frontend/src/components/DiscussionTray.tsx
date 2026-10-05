/* 讨论区（M9）：右栏第三个页签。
   讨论是挂 project 的自由主题多轮聊天：一说就记一对，回合不挂在任何一轮上。
   判断全在 `state/discussions.ts` 的纯函数里（能不能追问、能不能收卡、默认请谁），
   组件只把结果画出来、把动作递出去。 */

import { useEffect, useState } from "react";

import type { ThreadView } from "../api/types";
import {
  canAssemble,
  canDiscuss,
  cardKeepable,
  DISCUSSION_TURN_LIMIT,
  resolveRoleId,
  roleChoices,
  roleOptionOf,
  threadStatusText,
  turnsLeft,
} from "../state/discussions";
import type { DiscussionAnchor, DiscussionRoleOption } from "../state/discussions";
import { EvidenceChip } from "./EvidenceChip";

export function DiscussionTray({
  threads,
  /** 开聊卡上的角色选项：由 `roleOptions(schema)` 派生（真相源在后端 roles.toml）。 */
  roles: roleOptionsProp,
  anchor = null,
  pending,
  onStart,
  onSay,
  onCards,
  onKeep,
  onClose,
}: {
  threads: ThreadView[];
  /** 角色选项（`/schema` 的 `discussion_roles`，或离线回落那一条）。 */
  roles: DiscussionRoleOption[];
  /** 问卷里点「聊聊」带过来的题面与问的人（讨论 tab 自由开聊时为 null）。 */
  anchor?: DiscussionAnchor | null;
  /** 有讨论动作在途（开聊 / 说 / 总结 / 收卡 / 关掉）：按钮先禁掉，防连点。 */
  pending: boolean;
  onStart: (anchor: { topic: string; role_id: string }) => void;
  onSay: (threadId: string, text: string) => void;
  onCards: (threadId: string) => void;
  onKeep: (threadId: string, cardId: string) => void;
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

  const roles = roleChoices(anchorRole, roleOptionsProp);
  const trimmed = topic.trim();
  // 选中项的 focus 当提示行（`roleOptionOf` 认不出就照实给空 focus，不编）
  const selectedFocus = roleOptionOf(roleId, roleOptionsProp).focus;

  return (
    <div className="loop__scroll">
      <div className="loop__head">
        <h2 className="loop__title">讨论区</h2>
      </div>
      <p className="muted small">
        讨论不写文档：它是挂在这个项目上的自由对话。聊出想法之后「总结成卡」，
        收下的卡才会作为材料流回下一轮（凭空推演的默认不勾选）。
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
        <fieldset className="seats">
          <legend className="loop__label">请谁</legend>
          {roles.map((role) => (
            <label
              className={roleId === role.role_id ? "seat seat--on" : "seat"}
              key={role.role_id}
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
                {role.focus !== "" && <span className="seat__focus">它管什么：{role.focus}</span>}
              </span>
            </label>
          ))}
        </fieldset>
        <div className="prop__foot">
          <button
            type="button"
            className="btn"
            disabled={pending || trimmed === ""}
            onClick={() => onStart({ topic: trimmed, role_id: resolveRoleId(roleId) })}
          >
            开聊
          </button>
        </div>
        {selectedFocus !== "" && (
          <p className="muted small">它管什么：{selectedFocus}</p>
        )}
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

            {canDiscuss(thread) ? (
              <>
                <label className="sr-only" htmlFor={`say-${thread.thread_id}`}>
                  说这一轮
                </label>
                <textarea
                  id={`say-${thread.thread_id}`}
                  className="q__text"
                  value={draft}
                  placeholder={`跟${thread.role_title}再说一句（还能说 ${turnsLeft(thread)} 轮）`}
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
                  已经聊满（上限 {DISCUSSION_TURN_LIMIT} 条，一说是一对）：可以「总结成卡」，也可以关掉它。
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
                        {cardKeepable(card) ? (
                          <button
                            type="button"
                            className="btn btn--sm"
                            disabled={pending}
                            onClick={() => onKeep(thread.thread_id, card.card_id)}
                          >
                            收下为材料
                          </button>
                        ) : (
                          <span className="muted small">已收下为材料</span>
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
