import { describe, expect, it } from "vitest";

import type { RoundView, WebRequestView } from "../api/types";
import {
  executedWebRequestCount,
  labelCounts,
  loopBadgeCount,
  needsLoopAttention,
  overLimitWebRequestCount,
  rewritesOf,
  roundElapsed,
  roundOrdinal,
  statusText,
  webExecutionSlots,
  webRequestBadge,
} from "./progress";

describe("statusText（状态行那一句：waiting 优先于 step）", () => {
  function at(over: Partial<RoundView>) {
    return { step: "started", waiting_on: "none", ...over } as unknown as RoundView;
  }

  it("写完了只说一句（不再叠「没人需要动手」）", () => {
    expect(statusText(at({ step: "done", waiting_on: "none" }))).toBe("这一轮写完了");
  });

  it("waiting 优先于 step：只说「等你交问卷」，不叠「等你回答」", () => {
    expect(statusText(at({ step: "awaiting_answers", waiting_on: "questionnaire" }))).toBe(
      "等你交问卷",
    );
    expect(statusText(at({ step: "awaiting_confirm", waiting_on: "draft_card" }))).toBe("等你勾草案");
    expect(statusText(at({ step: "claims_ready", waiting_on: "opening_card" }))).toBe(
      "等你在复述卡上纠正",
    );
  });

  it("席位在跑就说在跑（跑动中报 waiting 会误导）", () => {
    expect(statusText(at({ step: "seats_running", waiting_on: "questionnaire" }))).toBe("席位在跑");
  });

  it("失败只说失败（原因由失败行单独说）", () => {
    expect(statusText(at({ step: "failed", waiting_on: "none" }))).toBe("这一轮失败了");
  });

  it("放弃交给失败行：这里给空串（不叠「这一轮已放弃」）", () => {
    expect(statusText(at({ step: "abandoned", waiting_on: "none" }))).toBe("");
  });

  it("其余回落 stepText（认得的 step 不编词）", () => {
    expect(statusText(at({ step: "started", waiting_on: "none" }))).toBe("刚开始");
    expect(statusText(at({ step: "claims_ready", waiting_on: "none" }))).toBe("已经复述完你的话");
  });
});

describe("roundOrdinal", () => {
  const rounds = [{ round_id: "r3" }, { round_id: "r2" }, { round_id: "r1" }];

  it("最新在前：第 1 轮在最前面，序号 = 总数 − 位置", () => {
    expect(roundOrdinal(rounds, "r3")).toBe(3);
    expect(roundOrdinal(rounds, "r2")).toBe(2);
    expect(roundOrdinal(rounds, "r1")).toBe(1);
  });

  it("看历史轮也算得出它是第几轮", () => {
    expect(roundOrdinal(rounds, "r1")).toBe(1);
  });

  it("不在列表里 / 没有轮次就是 null（界面回落，不编号）", () => {
    expect(roundOrdinal(rounds, "r9")).toBeNull();
    expect(roundOrdinal(rounds, null)).toBeNull();
    expect(roundOrdinal([], "r1")).toBeNull();
  });
});

describe("labelCounts", () => {
  it("按出现多少排标签", () => {
    const round = {
      claims: [{ label: "need" }, { label: "need" }, { label: "fact" }],
    } as unknown as RoundView;
    expect(labelCounts(round)).toEqual([
      ["need", 2],
      ["fact", 1],
    ]);
  });
});

describe("rewritesOf", () => {
  it("改判 = op 不是 add 的条数", () => {
    const draft = {
      proposals: [{ op: "add" }, { op: "replace" }, { op: "remove" }],
    };
    expect(rewritesOf(draft)).toBe(2);
    expect(rewritesOf(null)).toBe(0);
  });
});

describe("needsLoopAttention（右栏任务驱动显隐，06 §2.4 §3）", () => {
  function round(over: Partial<RoundView>) {
    return { step: "started", waiting_on: "none", ...over } as unknown as RoundView;
  }

  it("有问卷待答就展开", () => {
    expect(needsLoopAttention(round({ waiting_on: "questionnaire" }))).toBe(true);
  });

  it("开场卡与草案待确认也展开", () => {
    expect(needsLoopAttention(round({ waiting_on: "opening_card" }))).toBe(true);
    expect(needsLoopAttention(round({ waiting_on: "draft_card" }))).toBe(true);
  });

  it("无 round 就折叠", () => {
    expect(needsLoopAttention(null)).toBe(false);
  });

  it("waiting_on=none 就折叠", () => {
    expect(needsLoopAttention(round({ waiting_on: "none" }))).toBe(false);
  });

  it("席位在跑也展开（waiting_on 是 none，但 step 可判）", () => {
    expect(needsLoopAttention(round({ step: "seats_running", waiting_on: "none" }))).toBe(true);
  });

  it("终态不展开（failed/abandoned 配 none 仍折叠）", () => {
    expect(needsLoopAttention(round({ step: "failed", waiting_on: "none" }))).toBe(false);
    expect(needsLoopAttention(round({ step: "abandoned", waiting_on: "none" }))).toBe(false);
  });
});

describe("loopBadgeCount（rail 徽标：未答题数 + 草案组 0/1）", () => {
  function round(over: Partial<RoundView>) {
    return {
      step: "awaiting_answers",
      waiting_on: "questionnaire",
      questions: [],
      answer_sets: [],
      draft: null,
      ...over,
    } as unknown as RoundView;
  }

  it("徽标数=未答题数+草案组", () => {
    const questions = [{ question_id: "q1" }, { question_id: "q2" }, { question_id: "q3" }];
    const r = round({
      questions: questions as unknown as RoundView["questions"],
      answer_sets: [{ form_id: "f1", answered: 1, skipped: 0, answers: [] }],
      draft: { draft_id: "d1", groups: [], proposals: [], gaps: [] },
    });
    expect(loopBadgeCount(r)).toBe(3); // 2 未答 + 1 草案组
  });

  it("无 round 就是 0", () => {
    expect(loopBadgeCount(null)).toBe(0);
  });

  it("没草案时只有未答题数；题全答完徽标归零", () => {
    const one = [{ question_id: "q1" }] as unknown as RoundView["questions"];
    expect(loopBadgeCount(round({ questions: one }))).toBe(1);
    expect(loopBadgeCount(round({ questions: [] }))).toBe(0);
  });

  it("非任务态一律归零：done 轮 draft 残留也不计数", () => {
    const questions = [{ question_id: "q1" }, { question_id: "q2" }];
    const r = round({
      step: "done",
      waiting_on: "none",
      questions: questions as unknown as RoundView["questions"],
      draft: { draft_id: "d1", groups: [], proposals: [], gaps: [] },
    });
    expect(loopBadgeCount(r)).toBe(0);
  });
});
describe("联网申请徽标（P3a）", () => {
  function request(id: string, state: string): WebRequestView {
    return {
      request_id: id,
      query: `查 ${id}`,
      why: "为什么查",
      origin: "seat",
      seat_id: null,
      pass_index: null,
      state,
      result_note: null,
      created_at: null,
    };
  }

  function round(requests: WebRequestView[]): RoundView {
    return { web_requests: requests } as unknown as RoundView;
  }

  it("只数还没走完的申请（proposed + approved）", () => {
    const view = round([
      request("w1", "proposed"),
      request("w2", "approved"),
      request("w3", "done"),
      request("w4", "failed"),
      request("w5", "rejected"),
    ]);

    expect(webRequestBadge(view)).toBe("有 2 条联网申请");
  });

  it("没有待处理申请就不出徽标（0 不渲染）", () => {
    expect(webRequestBadge(round([]))).toBeNull();
    expect(webRequestBadge(round([request("w1", "done")]))).toBeNull();
    expect(webRequestBadge(null)).toBeNull();
  });

  it("执行名额计数 = approved + done + failed（proposed/rejected 不占）", () => {
    const view = round([
      request("w1", "proposed"),
      request("w2", "approved"),
      request("w3", "done"),
      request("w4", "failed"),
      request("w5", "rejected"),
    ]);

    expect(executedWebRequestCount(view)).toBe(3);
    expect(executedWebRequestCount(null)).toBe(0);
  });

  it("超额待恢复 = used>limit 时仍 approved 的条数；没超额 / limit 未知是 0", () => {
    const view = round([
      request("w1", "done"),
      request("w2", "done"),
      request("w3", "done"),
      request("w4", "approved"),
      request("w5", "approved"),
      request("w6", "rejected"),
    ]);

    expect(overLimitWebRequestCount(view, 3)).toBe(2);
    expect(overLimitWebRequestCount(view, 5)).toBe(0);
    expect(overLimitWebRequestCount(view, null)).toBe(0);
    expect(overLimitWebRequestCount(null, 3)).toBe(0);
  });

  it("剩余名额 = limit − 已用 − 已勾；limit 没读到时是 null", () => {
    expect(webExecutionSlots(0, 2, 2)).toEqual({ used: 0, selected: 2, remaining: 0 });
    expect(webExecutionSlots(1, 0, 3)).toEqual({ used: 1, selected: 0, remaining: 2 });
    expect(webExecutionSlots(3, 1, 3)).toEqual({ used: 3, selected: 1, remaining: 0 });
    expect(webExecutionSlots(0, 0, null)).toEqual({
      used: 0,
      selected: 0,
      remaining: null,
    });
  });
});

describe("roundElapsed（开局时长，rounds 列表拼）", () => {
  const rounds = [{ round_id: "r1", created_at: "2026-10-13T10:00:00.000Z" }];
  const now = Date.parse("2026-10-13T10:03:20.000Z");

  it("分钟数照实算（口径是「开局」——含等人时间，不说「已跑」）", () => {
    expect(roundElapsed(rounds, "r1", now)).toBe("开局 3 分钟");
  });

  it("不到 1 分钟不编 0（防瞎编时长）", () => {
    expect(roundElapsed(rounds, "r1", Date.parse("2026-10-13T10:00:30.000Z"))).toBe("刚刚开局");
  });

  it("找不到 / 时间在未来就是 null（界面回落，不显示）", () => {
    expect(roundElapsed(rounds, "r9", now)).toBeNull();
    expect(roundElapsed([], "r1", now)).toBeNull();
    expect(roundElapsed(rounds, "r1", Date.parse("2026-10-13T09:00:00.000Z"))).toBeNull();
  });

  it("超过 1 天按天说（防'已跑 28805 分钟'这种谎言）", () => {
    expect(roundElapsed(rounds, "r1", Date.parse("2026-10-16T10:00:00.000Z"))).toBe("开局 3 天");
    expect(roundElapsed(rounds, "r1", Date.parse("2026-10-13T12:00:00.000Z"))).toBe("开局 2 小时");
  });
});
