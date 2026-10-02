import { describe, expect, it } from "vitest";

import type { RoundView } from "../api/types";
import { failureLine } from "./failure";

function round(over: Partial<RoundView> = {}): RoundView {
  return {
    round_id: "r1",
    project_id: "p1",
    user_input: "想做个订单导出",
    step: "awaiting_confirm",
    waiting_on: "draft_card",
    seat_round_count: 1,
    seats: [],
    active_seats: [],
    nominated_seats: [],
    available_seats: [],
    claims: [],
    seat_packets: [],
    dropped: [],
    questions: [],
    list_items: [],
    draft: null,
    advisories: [],
    failure_reason: null,
    failure_at_step: null,
    retry_from: null,
    abandon_reason: null,
    confirmed_version_no: null,
    form_id: null,
    answer_sets: [],
    materials: [],
    web_queries: [],
    web_queries_chosen: [],
    web_search_available: false,
    write_scope: [],
    contradictions: [],
    proposed_offered: null,
    proposed_accepted: null,
    proposal_ignore_streak: 0,
    ...over,
  } as RoundView;
}

describe("failureLine（P29 眼睛检查 F8）", () => {
  it("正常一轮没有这一行", () => {
    expect(failureLine(round())).toBeNull();
  });

  it("失败且后端给了「从哪继续」→ 说清从哪继续", () => {
    const line = failureLine(
      round({
        step: "failed",
        failure_reason: "整合两次都过不了校验",
        failure_at_step: "seats_running",
        retry_from: "从席位轮继续（已交回的不重跑）",
      }),
    );

    expect(line?.kind).toBe("failed");
    expect(line?.text).toContain("整合两次都过不了校验");
    expect(line?.text).toContain("从席位轮继续");
  });

  it("认不出失败点（老库 / 没记录的 step）→ **只说原因，不猜**", () => {
    const line = failureLine(
      round({ step: "failed", failure_reason: "网关超时", failure_at_step: null, retry_from: null }),
    );

    expect(line?.text).toBe("网关超时");
    expect(line?.text).not.toContain("继续");
  });

  it("放弃是终点：写原因，不提重试", () => {
    const line = failureLine(
      round({ step: "abandoned", abandon_reason: "用户不要这一轮了" }),
    );

    expect(line?.kind).toBe("abandoned");
    expect(line?.text).toBe("用户不要这一轮了");
  });

  it("放弃但没写原因也说人话（老库）", () => {
    expect(failureLine(round({ step: "abandoned" }))?.text).toBe("这一轮已放弃");
  });
});
