import type { RoundView } from "../api/types";
import { failureLine } from "../state/failure";
import { statusText } from "../state/progress";

export function RoundStatus({
  round,
  pending,
  elapsed = null,
  onRetry,
  onAbandon,
}: {
  round: RoundView;
  pending: string | null;
  /** 这一轮跑了多久（LoopPane 用 rounds 列表算好传进来；null 就不显示）。 */
  elapsed?: string | null;
  onRetry: () => void;
  onAbandon: () => void;
}) {
  const running = pending !== null;
  const text = statusText(round);
  // 能放弃的时机：有人正在跑，或者这一轮停在"席位在跑 / 刚抽完主张 / 刚要复述"（可能卡在模型上）
  const canAbandon =
    pending !== null ||
    round.step === "seats_running" ||
    round.step === "claims_ready" ||
    round.step === "started";
  // 「接着跑」只在**真能继续**时出现：后端 RESUME_ROUTES 里只有这些 step 路由回
  // analyze / seat_round；claims_ready、awaiting_*、done、abandoned 全 → END（点了是空动作）。
  const canResume =
    !running &&
    (round.step === "failed" || round.step === "started" || round.step === "seats_running");
  const failure = failureLine(round);

  return (
    <div className="statusline">
      {text !== "" && <span className="statusline__step">{text}</span>}
      {running && round.step !== "seats_running" && (
        <span className="statusline__wait">正在跑…</span>
      )}
      {running && elapsed !== null && <span className="statusline__meta">{elapsed}</span>}
      {canResume && (
        <button
          type="button"
          className="btn btn--sm"
          onClick={onRetry}
          title="已经交回的席位不会重跑"
        >
          接着跑这一轮
        </button>
      )}
      {canAbandon && round.step !== "failed" && round.step !== "done" && (
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={onAbandon}
          title="不要这一轮了：会取消正在跑的模型调用，文档一个字节都不改"
        >
          放弃这一轮
        </button>
      )}
      {failure !== null && (
        <span className="statusline__fail">
          {failure.kind === "abandoned" ? "已放弃：" : "失败："}
          {failure.text}
        </span>
      )}
    </div>
  );
}
