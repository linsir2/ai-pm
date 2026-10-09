import type { LimitsView, RoundView } from "../api/types";
import { roundProgress } from "../state/progress";

/** 本轮执行进度（竖排 rail）：七格都从这一轮的字段推出来，没有一格是估的。
    序号是"排在第几格"，done / active / todo 三种状态由 `roundProgress` 给。 */
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
      {steps.map((step, order) => (
        <li
          className={`step step--${step.state}`}
          key={step.key}
          aria-current={step.state === "active" ? "step" : undefined}
          title={step.detail === null ? step.text : `${step.text}：${step.detail}`}
        >
          <span className="step__dot" aria-hidden="true">
            {step.state === "done" ? "✓" : order + 1}
          </span>
          <span className="step__body">
            <span className="step__text">{step.text}</span>
            {step.detail !== null && <span className="step__detail">{step.detail}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}
