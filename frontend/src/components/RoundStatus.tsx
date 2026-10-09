import type { RoundView } from "../api/types";
import { stepText, waitingText } from "../labels";
import { failureLine } from "../state/failure";

export function RoundStatus({
  round,
  pending,
  onRetry,
  onAbandon,
}: {
  round: RoundView;
  pending: string | null;
  onRetry: () => void;
  onAbandon: () => void;
}) {
  const running = pending !== null;
  // 能放弃的时机：有人正在跑，或者这一轮停在"席位在跑 / 刚抽完主张"（可能卡在模型上）
  const canAbandon =
    pending !== null ||
    round.step === "seats_running" ||
    round.step === "claims_ready" ||
    round.step === "started";
  // 失败的原因写在下面一行，这时再报"在等谁"只会让人误以为还有人要动手。
  const waiting =
    round.step === "seats_running" || round.step === "failed"
      ? null
      : waitingText(round.waiting_on);
  const failure = failureLine(round);

  return (
    <div className="statusline">
      <span className="statusline__step">{stepText(round.step)}</span>
      {running && <span className="statusline__wait">正在跑…</span>}
      {waiting !== null && <span className="statusline__wait">· {waiting}</span>}
      <span className="muted small">席位轮第 {round.seat_round_count} 次</span>
      <button
        type="button"
        className="btn btn--sm"
        onClick={onRetry}
        disabled={running}
        title="已经交回的席位不会重跑"
      >
        接着跑这一轮
      </button>
      {canAbandon && round.step !== "failed" && round.step !== "done" && (
        <button
          type="button"
          className="btn btn--sm btn--ghost"
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
