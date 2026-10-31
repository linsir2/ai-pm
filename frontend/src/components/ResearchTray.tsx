import { useState } from "react";

import type { ExplorationView, RoundView } from "../api/types";
import {
  buildResearchBody,
  canStartResearch,
  hasAnswer,
  researchStatusText,
  researchTopicError,
  sourceAction,
  sourceActionReason,
} from "../state/research";

/**
 * 「研究」页签（P3b-1）：只读结果 + 来源挂当前轮。
 *
 * 组件不做业务判断：能不能开、哪张卡能挂、为什么灰着，全部来自 `state/research.ts`
 * 的纯函数；数据一律是后端 `ExplorationView` 的真身，这里不拼不猜。
 */
export function ResearchTray({
  runs,
  pending,
  busy = false,
  busyText = "正在处理…",
  topicLimit,
  sourceLimit,
  round,
  onStart,
  onRetry,
  onAbandon,
  onAttach,
}: {
  runs: ExplorationView[];
  pending: boolean;
  /** 有任何动作在途（全局串行闸）：开始研究也禁掉，并写明正在做什么。 */
  busy?: boolean;
  /** busy 时的界面说法（由 `pendingActionLabel` 派生，组件不自己拼文案）。 */
  busyText?: string;
  topicLimit?: number;
  /** 一轮最多挂几张来源卡（拿不到就不拦，靠后端 422）。 */
  sourceLimit?: number;
  round: RoundView | null;
  onStart: (topic: string) => void;
  onRetry: (explorationId: string) => void;
  onAbandon: (explorationId: string) => void;
  onAttach: (explorationId: string, eids: string[]) => void;
}) {
  const [topic, setTopic] = useState("");
  const error = researchTopicError(topic, topicLimit);
  const canStart = canStartResearch(runs, pending) && error === null && !busy;

  return (
    <div className="loop__scroll">
      <div className="loop__head">
        <h2 className="loop__title">研究</h2>
      </div>
      <p className="muted small">
        自己去外面查：先搜再读，最多收 5 条来源，写成几条结论。结果只读——
        想让它进这一轮，把来源卡挂上来。
      </p>

      <section className="loop__group">
        <label className="loop__label" htmlFor="research-topic">
          研究什么
        </label>
        <textarea
          id="research-topic"
          className="q__text"
          rows={3}
          value={topic}
          maxLength={topicLimit}
          placeholder="例：同类产品的批量导出上限与实现方式"
          onChange={(event) => setTopic(event.target.value)}
        />
        <div className="loop__actions">
          <button
            type="button"
            className="btn btn--sm"
            disabled={!canStart}
            onClick={() => onStart(buildResearchBody(topic).topic)}
          >
            {busy ? busyText : pending ? "正在研究…" : "开始研究"}
          </button>
          {error !== null && <span className="input__hint">{error}</span>}
          {error === null && !canStartResearch(runs, pending) && (
            <span className="input__hint">同一项目同时只允许一趟研究。</span>
          )}
        </div>
      </section>

      {runs.length === 0 ? (
        <p className="muted small">这个项目还没跑过研究。</p>
      ) : (
        runs.map((run) => (
          <section className="loop__group" key={run.exploration_id}>
            <p className="loop__label">{researchStatusText(run)}</p>
            <p className="muted small">主题：{run.scope_text ?? "（没写）"}</p>

            {run.status === "failed" && (
              <div className="loop__actions">
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={pending}
                  onClick={() => onRetry(run.exploration_id)}
                >
                  重跑
                </button>
              </div>
            )}

            {run.status === "done" && (
              <>
                {hasAnswer(run) ? (
                  <p>
                    {run.research?.answer}
                    {(run.research?.answer_refs ?? []).length > 0 && (
                      <span className="muted small">
                        {" "}
                        〔{(run.research?.answer_refs ?? []).join("、")}〕
                      </span>
                    )}
                  </p>
                ) : (
                  <p className="muted small">这一趟只找到来源</p>
                )}

                {(run.research?.findings ?? []).length > 0 && (
                  <ul className="list">
                    {(run.research?.findings ?? []).map((finding) => (
                      <li key={finding.finding_id}>
                        {finding.statement}
                        <span className="muted small"> 〔{finding.refs.join("、")}〕</span>
                      </li>
                    ))}
                  </ul>
                )}

                {(run.research?.sources ?? []).length > 0 && (
                  <>
                    <p className="loop__label">来源</p>
                    <ul className="list">
                      {(run.research?.sources ?? []).map((source) => {
                        const action = sourceAction(source, round);
                        const attached = round?.research_sources?.length ?? 0;
                        const full =
                          sourceLimit !== undefined && attached >= sourceLimit;
                        const reason =
                          action === "attach" && full
                            ? "这一轮已经挂满了"
                            : sourceActionReason(action, round);
                        return (
                          <li key={source.eid}>
                            <span className="muted small">〔{source.eid}〕</span>{" "}
                            {source.title}
                            <div className="muted small">{source.url}</div>
                            <div className="loop__actions">
                              <button
                                type="button"
                                className="btn btn--ghost btn--sm"
                                disabled={action !== "attach" || pending || full}
                                onClick={() =>
                                  onAttach(run.exploration_id, [source.eid])
                                }
                              >
                                {action === "attached" ? "已挂到当前轮" : "挂到当前轮"}
                              </button>
                              {reason !== null && (
                                <span className="input__hint">{reason}</span>
                              )}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  </>
                )}

                {(run.research?.open_questions ?? []).length > 0 && (
                  <>
                    <p className="loop__label">还没答的问题</p>
                    <ul className="list">
                      {(run.research?.open_questions ?? []).map((question) => (
                        <li key={question}>{question}</li>
                      ))}
                    </ul>
                  </>
                )}

                {(run.research?.notes ?? []).length > 0 && (
                  <p className="muted small">{(run.research?.notes ?? []).join("；")}</p>
                )}
              </>
            )}

            {run.status === "running" && (
              <div className="loop__actions">
                <button
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={pending}
                  onClick={() => onAbandon(run.exploration_id)}
                >
                  放弃
                </button>
              </div>
            )}
          </section>
        ))
      )}
    </div>
  );
}
