import type { RoundView } from "../api/types";
import { roundProgress } from "../state/progress";

/** 本轮执行进度：七格都从这一轮的字段推出来，没有一格是估的。 */
export function ProgressSteps({ round }: { round: RoundView | null }) {
  const steps = roundProgress(round);
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
