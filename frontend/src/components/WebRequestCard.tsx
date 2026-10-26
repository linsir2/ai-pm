/* 一条联网申请（P3a）：谁提的、查什么、为什么、到哪一步了。
   批准即执行；能不能批由 `webRequestAction` 判，组件只照结果画。 */

import { useId } from "react";

import { webOriginText, webRequestAction, webRequestStateText } from "../api/payload";
import type { WebRequestView } from "../api/types";

export type WebRequestPendingAction = "approve" | "reject" | "execute" | "abandon";

export function WebRequestCard({
  request,
  used,
  limit,
  available,
  busy,
  pendingDecision,
  historical,
  terminal,
  networkAccess,
  onDecide,
  onRecover,
}: {
  request: WebRequestView;
  /** 本轮已占用的执行名额。 */
  used: number;
  /** `/schema.limits.max_web_executions`；null = 还没读到。 */
  limit: number | null;
  /** 服务端现在有没有可用的联网工具（没配密钥就批不了）。 */
  available: boolean;
  /** 轮内还有别的动作在跑。 */
  busy: boolean;
  /** 这一条自己的决策/恢复请求在途，以及被点的是哪一个动作。 */
  pendingDecision: WebRequestPendingAction | null;
  /** 正在看历史轮（只读）。 */
  historical: boolean;
  /** 轮已终态。 */
  terminal: boolean;
  /** 联网工具声明会外发（第三方披露）。 */
  networkAccess: boolean;
  onDecide: (decision: "approve" | "reject") => void;
  /** 超额 approved 的逐条恢复（P3b）：执行一次或放弃。 */
  onRecover?: (action: "execute" | "abandon") => void;
}) {
  const pending = pendingDecision !== null;
  const action = webRequestAction(request, {
    used,
    limit,
    available,
    busy,
    pending,
    historical,
    terminal,
  });
  const reasonId = useId();
  const describedBy = (reason: string | null) =>
    !pending && reason !== null ? reasonId : undefined;
  const meta = [
    webOriginText(request.origin),
    request.seat_id,
    request.pass_index === null ? null : `第 ${request.pass_index} 趟`,
  ].filter((part): part is string => part !== null && part.trim() !== "");
  const reasons = Array.from(
    new Set(
      [action.approve.reason, action.reject.reason].filter(
        (reason): reason is string => reason !== null,
      ),
    ),
  );
  const note = request.result_note?.trim() ?? "";
  const recoveryReasons = Array.from(
    new Set(
      [action.recovery.execute.reason, action.recovery.abandon.reason].filter(
        (reason): reason is string => reason !== null,
      ),
    ),
  );
  const PENDING_TEXT: Record<WebRequestPendingAction, string> = {
    approve: "批准",
    reject: "拒绝",
    execute: "执行",
    abandon: "放弃",
  };

  return (
    <article className="web-request" aria-label={`联网申请：${request.query}`}>
      <header className="web-request__head">
        <p className="web-request__query">{request.query}</p>
        <span className="tag">{webRequestStateText(request.state)}</span>
      </header>

      {meta.length > 0 && <p className="web-request__meta muted small">{meta.join(" · ")}</p>}
      {request.why.trim() !== "" && (
        <p className="web-request__why">为什么查：{request.why}</p>
      )}
      {note !== "" && <p className="web-request__note">结果：{note}</p>}

      {action.visible && networkAccess && (
        <p className="web-request__note">
          批准后检索词会发给第三方搜索服务；确认里面没有不想外发的内容。
        </p>
      )}

      {action.recovery.visible && (
        <p className="web-request__note">
          本轮已超联网名额（{used}/{limit}）：这条已批准但不会再自动执行，请逐条处理。
        </p>
      )}

      {action.recovery.visible && (
        <div className="web-request__actions">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={!action.recovery.execute.enabled || pending}
            aria-describedby={describedBy(action.recovery.execute.reason)}
            title={action.recovery.execute.reason ?? undefined}
            onClick={() => onRecover?.("execute")}
          >
            {pendingDecision === "execute" ? "处理中…" : "执行（临时超额）"}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={!action.recovery.abandon.enabled || pending}
            aria-describedby={describedBy(action.recovery.abandon.reason)}
            title={action.recovery.abandon.reason ?? undefined}
            onClick={() => onRecover?.("abandon")}
          >
            {pendingDecision === "abandon" ? "处理中…" : "放弃（不计名额）"}
          </button>
          {pendingDecision !== null ? (
            <span className="web-request__reason muted small" role="status" aria-live="polite">
              正在处理：{PENDING_TEXT[pendingDecision]}…
            </span>
          ) : (
            recoveryReasons.length > 0 && (
              <span className="web-request__reason muted small" id={reasonId}>
                {recoveryReasons.join("；")}
              </span>
            )
          )}
        </div>
      )}

      {action.visible && (
        <div className="web-request__actions">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={!action.approve.enabled || pending}
            aria-describedby={describedBy(action.approve.reason)}
            title={action.approve.reason ?? undefined}
            onClick={() => onDecide("approve")}
          >
            {pendingDecision === "approve" ? "处理中…" : "批准并查"}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={!action.reject.enabled || pending}
            aria-describedby={describedBy(action.reject.reason)}
            title={action.reject.reason ?? undefined}
            onClick={() => onDecide("reject")}
          >
            {pendingDecision === "reject" ? "处理中…" : "拒绝"}
          </button>
          {pendingDecision !== null ? (
            <span className="web-request__reason muted small" role="status" aria-live="polite">
              正在处理：{PENDING_TEXT[pendingDecision]}…
            </span>
          ) : (
            reasons.length > 0 && (
              <span className="web-request__reason muted small" id={reasonId}>
                {reasons.join("；")}
              </span>
            )
          )}
        </div>
      )}
    </article>
  );
}
