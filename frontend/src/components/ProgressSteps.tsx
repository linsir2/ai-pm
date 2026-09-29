import type { LimitsView, RoundView } from "../api/types";
import { roundProgress } from "../state/progress";

/** 本轮执行进度：七格都从这一轮的字段推出来，没有一格是估的。 */
export function ProgressSteps({
  round,
  limits = null,
}: {
  round: RoundView | null;
  /** 席位轮上限等常量：来自 `/schema`（拿不到就由 `progress` 回落）。 */
  limits?: LimitsView | null;
}) {
  const steps = roundProgress(round, limits);
  if (steps.length === 0) return null;

  return (
    <ol className="steps" aria-label="本轮执行进度">
      {steps.map((step) => (
        <li
          className={`step step--${step.state}`}
          key={step.key}
          aria-current={step.state === "active" ? "step" : undefined}
          title={step.detail === null ? step.text : `${step.text}：${step.detail}`}
        >
          <span className="step__dot" aria-hidden="true" />
          <span className="step__text">{step.text}</span>
          {step.detail !== null && <span className="step__detail">{step.detail}</span>}
        </li>
      ))}
    </ol>
  );
}
