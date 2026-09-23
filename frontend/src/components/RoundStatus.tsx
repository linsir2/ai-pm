import type { RoundView } from "../api/types";
import { stepText, waitingText } from "../labels";

export function RoundStatus({
  round,
  pending,
  onRetry,
}: {
  round: RoundView;
  pending: string | null;
  onRetry: () => void;
}) {
  const running = pending !== null;
  // 失败的原因写在下面一行，这时再报"在等谁"只会让人误以为还有人要动手。
  const waiting =
    round.step === "seats_running" || round.step === "failed"
      ? null
      : waitingText(round.waiting_on);

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
        title="接着跑这一轮；已经交回的席位不会重跑"
      >
        继续/重试
      </button>
      {round.failure_reason !== null && (
        <span className="statusline__fail">失败原因：{round.failure_reason}</span>
      )}
    </div>
  );
}
