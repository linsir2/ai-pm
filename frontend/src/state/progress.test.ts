import { describe, expect, it } from "vitest";

import type { RoundView } from "../api/types";
import { labelCounts, rewritesOf, roundProgress } from "./progress";

const empty = {
  round_id: "r1",
  step: "claims_ready",
  waiting_on: "opening_card",
  seat_round_count: 0,
  seats: [],
  claims: [],
  questions: [],
  seat_packets: [],
  draft: null,
  confirmed_version_no: null,
} as unknown as RoundView;

function stepOf(round: RoundView, key: string) {
  return roundProgress(round).find((step) => step.key === key)!;
}

describe("roundProgress", () => {
  it("没有轮次就没有进度", () => {
    expect(roundProgress(null)).toEqual([]);
  });

  it("刚复述完：主张已出，复述在等你", () => {
    const round = { ...empty, claims: [{ eid: "E1", label: "need" }] } as unknown as RoundView;
    expect(stepOf(round, "claims").state).toBe("done");
    expect(stepOf(round, "claims").detail).toBe("1 条");
    expect(stepOf(round, "opening").state).toBe("active");
    expect(stepOf(round, "seats").state).toBe("todo");
  });

  it("席位跑完了、还没整合：整合在跑", () => {
    const round = {
      ...empty,
      step: "seats_running",
      waiting_on: "none",
      seats: ["seat.a"],
      seat_round_count: 1,
      seat_packets: [{ seat_id: "seat.a", status: "completed" }],
    } as unknown as RoundView;
    expect(stepOf(round, "seats").state).toBe("active");
    expect(stepOf(round, "seats").detail).toBe("1 / 3 轮");
    expect(stepOf(round, "integration").state).toBe("active");
  });

  it("出了问卷：问卷是当前那一步", () => {
    const round = {
      ...empty,
      step: "awaiting_answers",
      waiting_on: "questionnaire",
      seats: ["seat.a"],
      seat_round_count: 1,
      questions: [{ question_id: "q1" }],
      seat_packets: [{ seat_id: "seat.a", status: "completed" }],
    } as unknown as RoundView;
    expect(stepOf(round, "integration").state).toBe("done");
    expect(stepOf(round, "questionnaire").state).toBe("active");
    expect(stepOf(round, "draft").state).toBe("todo");
  });

  it("出了问卷但没草案：席位轮仍 active（答完可能再跑一轮）", () => {
    const round = {
      ...empty,
      step: "awaiting_answers",
      waiting_on: "questionnaire",
      seats: ["seat.a", "seat.b"],
      seat_round_count: 1,
      questions: [{ question_id: "q1" }],
      seat_packets: [
        { seat_id: "seat.a", status: "completed" },
        { seat_id: "seat.b", status: "completed" },
      ],
    } as unknown as RoundView;
    // 出了问卷 ≠ 席位轮完成；答完问卷可能触发第二轮席位
    expect(stepOf(round, "seats").state).toBe("active");
    expect(stepOf(round, "questionnaire").state).toBe("active");
  });

  it("没有问卷的路径：问卷那格写「没有要问的」，不是没做事", () => {
    const round = {
      ...empty,
      step: "awaiting_confirm",
      waiting_on: "draft_card",
      seats: ["seat.a"],
      seat_round_count: 1,
      seat_packets: [{ seat_id: "seat.a", status: "completed" }],
      draft: { proposals: [{ proposal_id: "p1", op: "add" }] },
    } as unknown as RoundView;
    expect(stepOf(round, "questionnaire").state).toBe("todo");
    expect(stepOf(round, "questionnaire").detail).toBe("没有要问的");
    expect(stepOf(round, "draft").state).toBe("active");
  });

  it("写完了：确认那格带上版本号", () => {
    const round = {
      ...empty,
      step: "done",
      waiting_on: "none",
      seats: ["seat.a"],
      seat_packets: [{ seat_id: "seat.a", status: "completed" }],
      draft: { proposals: [] },
      confirmed_version_no: 2,
    } as unknown as RoundView;
    expect(stepOf(round, "confirm").state).toBe("done");
    expect(stepOf(round, "confirm").detail).toBe("v2");
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

describe("roundProgress 的席位轮上限", () => {
  it("上限从 /schema 来，不再是前端抄的 3", () => {
    const round = {
      ...empty,
      seats: ["seat.a"],
      seat_round_count: 1,
      seat_packets: [{ seat_id: "seat.a", status: "completed" }],
    } as unknown as RoundView;

    const limits = { max_seat_rounds: 5, max_questionnaires: 2, max_open_items_per_batch: 3 };

    expect(roundProgress(round, limits).find((step) => step.key === "seats")!.detail).toBe(
      "1 / 5 轮",
    );
    // 没给上限就回落旧默认值（3），界面照样有数
    expect(roundProgress(round).find((step) => step.key === "seats")!.detail).toBe("1 / 3 轮");
  });
});
