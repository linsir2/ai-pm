/* 灵感箱（M12）：讨论聊出来的卡与调研带回来的候选，统一在这过目。
   不写文档、不回流——「收下」是唯一的回流路径；忽略与聊聊都不动文档。
   判断全在 `state/inbox.ts` / `state/explorations.ts` / `state/discussions.ts`
   的纯函数里，组件只把结果画出来、把动作递出去。 */

import type { CardView, CandidateView, ExplorationView, ThreadView } from "../api/types";
import { CANDIDATE_KIND_TEXT, EXPLORATION_STATUS_TEXT, REVIEW_ACTION_TEXT } from "../labels";
import { cardActions } from "../state/discussions";
import { inboxCards } from "../state/inbox";
import {
  candidateActions,
  failureText,
  isRunning,
  overflowNote,
  reviewCandidates,
  reviewExplorations,
} from "../state/explorations";
import { EvidenceChip } from "./EvidenceChip";

function scopeLabel(exploration: ExplorationView): string {
  const base =
    exploration.scope_kind !== "entry"
      ? "整份文档"
      : exploration.scope_ref === null
        ? "条目"
        : `条目 ${exploration.scope_ref}`;
  // 「拿去验证」（M13）来的趟次带焦点说明：显示出来才认得出它在验证谁
  const focus = exploration.scope_text?.trim() ?? "";
  return focus === "" ? base : `${base} · ${focus}`;
}

function cardGroundingText(card: CardView): string {
  return card.grounding === "document" ? "文档里有据" : "凭空推演";
}

export function InboxTray({
  threads,
  explorations,
  pending,
  onRetry,
  onAbandon,
  onKeepSeed,
  onKeepMaterial,
  onDismiss,
  onKeepCard,
  onDismissCard,
  onDiscuss,
  onVerify,
  onStartDocument,
}: {
  /** 讨论卡的数据源（state=new 的卡进箱）：全部线程，最新在前。 */
  threads: ThreadView[];
  explorations: ExplorationView[];
  /** 有动作在跑（重试 / 放弃 / 收下 / 忽略）：按钮先禁掉，防连点。 */
  pending: boolean;
  onRetry: (explorationId: string) => void;
  onAbandon: (explorationId: string) => void;
  onKeepSeed: (explorationId: string, candidate: CandidateView) => void;
  onKeepMaterial: (explorationId: string, candidate: CandidateView) => void;
  onDismiss: (explorationId: string, candidate: CandidateView) => void;
  onKeepCard: (threadId: string, cardId: string, as: "seed" | "material") => void;
  onDismissCard: (threadId: string, cardId: string) => void;
  /** 就这条聊聊：把原话交出去（上层设锚点并切到讨论 tab）。 */
  onDiscuss: (topic: string) => void;
  /** 拿去验证（M13）：拿卡片原话起一趟聚焦调研（候选照常回这个箱）。 */
  onVerify: (statement: string) => void;
  onStartDocument: () => void;
}) {
  const running = explorations.filter((item) => item.status === "running");
  const failed = explorations.filter((item) => item.status === "failed");
  const review = reviewExplorations(explorations);
  const reviewIds = new Set(review.map((item) => item.exploration_id));
  const recent = explorations.filter(
    (item) =>
      (item.status === "done" || item.status === "abandoned") &&
      !reviewIds.has(item.exploration_id),
  );
  const cards = inboxCards(threads);
  const empty =
    running.length === 0 && failed.length === 0 && review.length === 0 && cards.length === 0;
  const busy = pending || isRunning(explorations);

  return (
    <div className="loop__scroll">
      <div className="loop__head">
        <h2 className="loop__title">灵感箱</h2>
      </div>
      <p className="muted small">
        讨论聊出来的卡与调研带回来的候选都在这；「收下」是唯一的回流路径，其余什么都不动文档。
      </p>

      {empty && <p className="muted small">还没有可过目的东西</p>}

      {running.length > 0 && (
        <section className="loop__group">
          <p className="loop__label">进行中</p>
          {running.map((item) => (
            <p className="muted small" key={item.exploration_id}>
              调研在跑…（{scopeLabel(item)}）—— 跑完候选会出现在这里。
            </p>
          ))}
        </section>
      )}

      {failed.length > 0 && (
        <section className="loop__group">
          <p className="loop__label">失败</p>
          {failed.map((item) => (
            <article className="prop" key={item.exploration_id}>
              <div>
                <p className="prop__text">{failureText(item)}</p>
                <div className="prop__foot">
                  <span className="tag">{scopeLabel(item)}</span>
                  <button
                    type="button"
                    className="btn btn--sm"
                    disabled={pending}
                    onClick={() => onRetry(item.exploration_id)}
                  >
                    重试
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={pending}
                    onClick={() => onAbandon(item.exploration_id)}
                  >
                    放弃
                  </button>
                </div>
              </div>
            </article>
          ))}
        </section>
      )}

      {cards.length > 0 && (
        <section className="loop__group">
          <p className="loop__label">讨论里聊出来的</p>
          {cards.map(({ thread, card }) => (
            <article className="prop" key={`${thread.thread_id}:${card.card_id}`}>
              <div>
                <p className="prop__text">{card.statement}</p>
                <div className="prop__foot">
                  <span className="tag">{cardGroundingText(card)}</span>
                  <EvidenceChip eids={card.based_on} />
                  <span className="muted small">来自讨论：{thread.topic}</span>
                </div>
                <div className="prop__foot">
                  {cardActions(card).map((action) => (
                    <button
                      key={action}
                      type="button"
                      className={action === "dismiss" ? "btn btn--ghost btn--sm" : "btn btn--sm"}
                      disabled={pending}
                      onClick={() => {
                        if (action === "keep-seed") onKeepCard(thread.thread_id, card.card_id, "seed");
                        else if (action === "keep-material")
                          onKeepCard(thread.thread_id, card.card_id, "material");
                        else onDismissCard(thread.thread_id, card.card_id);
                      }}
                    >
                      {REVIEW_ACTION_TEXT[action]}
                    </button>
                  ))}
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={busy}
                    onClick={() => onVerify(card.statement)}
                  >
                    拿去验证
                  </button>
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={pending}
                    onClick={() => onDiscuss(card.statement)}
                  >
                    聊聊
                  </button>
                </div>
              </div>
            </article>
          ))}
        </section>
      )}

      {review.map((item) => (
        <section className="loop__group" key={item.exploration_id}>
          <p className="loop__label">调研带回来的 · {scopeLabel(item)}</p>
          {overflowNote(item) !== null && <p className="prop__warn">{overflowNote(item)}</p>}
          {reviewCandidates(item).map((candidate) => (
            <article className="prop" key={candidate.candidate_id}>
              <div>
                <p className="prop__text">{candidate.statement}</p>
                <div className="prop__foot">
                  <span className="tag">{CANDIDATE_KIND_TEXT[candidate.kind] ?? candidate.kind}</span>
                  <EvidenceChip eids={candidate.based_on} />
                </div>
                {(candidate.falsification !== null ||
                  candidate.evidence_needed !== null ||
                  candidate.next_step !== null) && (
                  <div className="prop__rationale">
                    {candidate.falsification !== null && (
                      <p>证伪它：{candidate.falsification}</p>
                    )}
                    {candidate.evidence_needed !== null && (
                      <p>需要证据：{candidate.evidence_needed}</p>
                    )}
                    {candidate.next_step !== null && <p>下一步：{candidate.next_step}</p>}
                  </div>
                )}
                <div className="prop__foot">
                  {candidateActions(candidate).map((action) => (
                    <button
                      key={action}
                      type="button"
                      className={action === "dismiss" ? "btn btn--ghost btn--sm" : "btn btn--sm"}
                      disabled={pending}
                      onClick={() => {
                        if (action === "keep-seed") onKeepSeed(item.exploration_id, candidate);
                        else if (action === "keep-material")
                          onKeepMaterial(item.exploration_id, candidate);
                        else onDismiss(item.exploration_id, candidate);
                      }}
                    >
                      {REVIEW_ACTION_TEXT[action]}
                    </button>
                  ))}
                  <button
                    type="button"
                    className="btn btn--ghost btn--sm"
                    disabled={pending}
                    onClick={() => onDiscuss(candidate.statement)}
                  >
                    聊聊
                  </button>
                </div>
              </div>
            </article>
          ))}
        </section>
      ))}

      {recent.length > 0 && (
        <section className="loop__group">
          <p className="loop__label">最近探索</p>
          <ul className="rounds__list">
            {recent.map((item) => (
              <li className="rounds__row" key={item.exploration_id}>
                <span className="muted small">
                  {scopeLabel(item)} ·{" "}
                  {EXPLORATION_STATUS_TEXT[item.status] ?? item.status}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="loop__actions">
        <button type="button" className="btn" disabled={busy} onClick={onStartDocument}>
          发起调研（整份文档）
        </button>
        {isRunning(explorations) && (
          <span className="input__hint">同一项目同时只允许一趟调研。</span>
        )}
      </div>
    </div>
  );
}
