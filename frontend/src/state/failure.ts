/* 失败/放弃那一行说人话（`P29` 眼睛检查 F8）。

   后端早就把「失败在哪个 step」与「重试会从哪继续」算好了（`RoundView.failure_at_step` /
   `retry_from`，口径见 `02` §11.0.21 口径四），界面一直没画——用户只能看到一句原因，
   不知道点重试会不会从头再来一遍。

   `retry_from` 是后端给的人话（认不出的 step 后端自己会回落成 `null`）；这里**不猜**：
   它空着就只报原因。 */

import type { RoundView } from "../api/types";
import { stepText } from "../labels";

export interface FailureLine {
  kind: "failed" | "abandoned";
  text: string;
}

export function failureLine(round: RoundView): FailureLine | null {
  if (round.step === "abandoned") {
    return { kind: "abandoned", text: round.abandon_reason ?? "这一轮已放弃" };
  }
  if (round.step !== "failed" && round.failure_reason === null) return null;
  const reason = round.failure_reason ?? "这一轮失败了";
  if (round.retry_from === null) return { kind: "failed", text: reason };
  const where = round.failure_at_step === null ? "" : `失败在${stepText(round.failure_at_step)}；`;
  return { kind: "failed", text: `${where}${reason}——点「继续/重试」会${round.retry_from}` };
}
