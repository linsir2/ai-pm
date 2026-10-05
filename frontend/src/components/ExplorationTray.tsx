/* 过目台（M8）：右栏「本轮 / 过目台」的后一半。
   调研只出候选；这里只做三件事——把结果摆出来、把收下/删掉递出去、给失败与在跑一个出口。
   判断全在 `state/explorations.ts` 的纯函数里，组件不做业务决定。 */

import type { CandidateView, ExplorationView } from "../api/types";
import { CANDIDATE_KIND_TEXT, EXPLORATION_STATUS_TEXT } from "../labels";
import {
  candidateActions,
  failureText,
  isRunning,
  overflowNote,
  trayCandidates,
  trayExplorations,
} from "../state/explorations";
import type { CandidateAction } from "../state/explorations";
import { EvidenceChip } from "./EvidenceChip";

const ACTION_TEXT: Record<CandidateAction, string> = {
  "keep-seed": "收下为下一轮输入",
  "keep-material": "收下为材料",
  dismiss: "删掉",
};

function scopeLabel(exploration: ExplorationView): string {
  if (exploration.scope_kind !== "entry") return "整份文档";
  return exploration.scope_ref === null ? "条目" : `条目 ${exploration.scope_ref}`;
}

export function ExplorationTray({
  explorations,
  pending,
  onRetry,
  onAbandon,
  onKeepSeed,
  onKeepMaterial,
  onDismiss,
  onStartDocument,
}: {
  explorations: ExplorationView[];
  /** 有探索动作在跑（重试 / 放弃 / 收下 / 删掉）：按钮先禁掉，防连点。 */
  pending: boolean;
  onRetry: (explorationId: string) => void;
  onAbandon: (explorationId: string) => void;
  onKeepSeed: (explorationId: string, candidate: CandidateView) => void;
  onKeepMaterial: (explorationId: string, candidate: CandidateView) => void;
  onDismiss: (explorationId: string, candidate: CandidateView) => void;
  onStartDocument: () => void;
}) {
  const running = explorations.filter((item) => item.status === "running");
  const failed = explorations.filter((item) => item.status === "failed");
  const review = trayExplorations(explorations);
  const reviewIds = new Set(review.map((item) => item.exploration_id));
  const recent = explorations.filter(
    (item) =>
      (item.status === "done" || item.status === "abandoned") &&
      !reviewIds.has(item.exploration_id),
  );
  const empty = running.length === 0 && failed.length === 0 && review.length === 0;
  const busy = pending || isRunning(explorations);

  return (
    <div className="loop__scroll">
      <div className="loop__head">
        <h2 className="loop__title">过目台</h2>
      </div>
      <p className="muted small">
        调研只出候选（假设 / 盲区）；「收下」是唯一的回流路径，其余什么都不动文档。
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

      {review.map((item) => (
        <section className="loop__group" key={item.exploration_id}>
          <p className="loop__label">{scopeLabel(item)}</p>
          {overflowNote(item) !== null && <p className="prop__warn">{overflowNote(item)}</p>}
          {trayCandidates(item).map((candidate) => (
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
                      {ACTION_TEXT[action]}
                    </button>
                  ))}
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
