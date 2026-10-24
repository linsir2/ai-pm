/* 一步检索的只读小条：命中多少、真正带进多少、失败就说失败。
   结构由 `state/retrieval.retrievalLine` 决定，组件只画。 */

import type { RetrievalView } from "../api/types";
import { retrievalLine } from "../state/retrieval";

export function RoundRetrieval({ trace }: { trace: RetrievalView }) {
  return (
    <p className={trace.ok ? "retrieval" : "retrieval retrieval--failed"}>
      <span className="muted small">{retrievalLine(trace)}</span>
      {trace.query.trim() !== "" && (
        <span className="retrieval__query muted small mono">「{trace.query}」</span>
      )}
    </p>
  );
}
