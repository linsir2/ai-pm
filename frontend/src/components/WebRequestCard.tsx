/* 一条联网申请（P3a）：谁提的、查什么、为什么、到哪一步了。
   批准即执行；能不能批由 `webRequestAction` 判，组件只照结果画。 */

import { webOriginText, webRequestAction, webRequestStateText } from "../api/payload";
import type { WebRequestView } from "../api/types";

export function WebRequestCard({
  request,
  used,
  limit,
  available,
  busy,
  pending,
  historical,
  terminal,
  networkAccess,
  onDecide,
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
  /** 这一条自己的决策请求在途。 */
  pending: boolean;
  /** 正在看历史轮（只读）。 */
  historical: boolean;
  /** 轮已终态。 */
  terminal: boolean;
  /** 联网工具声明会外发（第三方披露）。 */
  networkAccess: boolean;
  onDecide: (decision: "approve" | "reject") => void;
}) {
  const action = webRequestAction(request, {
    used,
    limit,
    available,
    busy,
    pending,
    historical,
    terminal,
  });
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

      {action.visible && (
        <div className="web-request__actions">
          <button
            type="button"
            className="btn btn--primary btn--sm"
            disabled={!action.approve.enabled || pending}
            title={action.approve.reason ?? undefined}
            onClick={() => onDecide("approve")}
          >
            {pending ? "处理中…" : "批准并查"}
          </button>
          <button
            type="button"
            className="btn btn--sm"
            disabled={!action.reject.enabled || pending}
            title={action.reject.reason ?? undefined}
            onClick={() => onDecide("reject")}
          >
            拒绝
          </button>
          {reasons.map((reason) => (
            <span className="web-request__reason muted small" key={reason}>
              {reason}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}
