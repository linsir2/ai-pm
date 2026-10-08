/* 过目台的纯函数（M8）：挑哪几趟上台、每条候选能做什么、收下后往输入框写什么。
   组件不许做这些判断——它只按这里的结果画。 */

import { describe, expect, it } from "vitest";

import type { CandidateView, ExplorationView } from "../api/types";
import {
  candidateActions,
  failureText,
  isRunning,
  overflowNote,
  seedText,
  reviewCandidates,
  reviewExplorations,
} from "./explorations";

function candidate(over: Partial<CandidateView> = {}): CandidateView {
  return {
    candidate_id: "H1",
    kind: "hypothesis",
    statement: "导出要支持按状态筛选",
    state: "new",
    kept_as: null,
    kept_ref: null,
    grounding: "document",
    based_on: ["E1"],
    falsification: "如果用户只导全量，就不需要筛选",
    evidence_needed: null,
    next_step: null,
    ...over,
  };
}

function exploration(over: Partial<ExplorationView> = {}): ExplorationView {
  return {
    exploration_id: "x-1",
    project_id: "p1",
    kind: "inquiry",
    status: "done",
    scope_kind: "document",
    scope_ref: null,
    scope_text: null,
    created_at: "2026-10-05T10:00:00+00:00",
    failure_reason: null,
    abandon_reason: null,
    overflow_count: 0,
    candidates: [candidate()],
    ...over,
  };
}

describe("reviewExplorations", () => {
  it("只挑还有 new 候选的探索，最新在前（进来的顺序就是后端的最新在前）", () => {
    const newer = exploration({ exploration_id: "x-new" });
    const older = exploration({ exploration_id: "x-old" });
    const settled = exploration({
      exploration_id: "x-settled",
      candidates: [candidate({ state: "kept", kept_as: "seed" })],
    });
    const running = exploration({ exploration_id: "x-run", status: "running", candidates: [] });

    expect(reviewExplorations([newer, settled, running, older]).map((item) => item.exploration_id)).toEqual(
      ["x-new", "x-old"],
    );
  });

  it("没有候选（跑挂 / 放弃 / 空手而归）的不上台", () => {
    const failed = exploration({ exploration_id: "x-fail", status: "failed", candidates: [] });
    const empty = exploration({ exploration_id: "x-empty", candidates: [] });

    expect(reviewExplorations([failed, empty])).toEqual([]);
  });
});

describe("reviewCandidates", () => {
  it("只回还没过目的（new），保持原顺序", () => {
    const kept = candidate({ candidate_id: "H1", state: "kept", kept_as: "material" });
    const fresh = candidate({ candidate_id: "B1", kind: "blind_spot" });
    const dismissed = candidate({ candidate_id: "H2", state: "dismissed" });

    expect(
      reviewCandidates(exploration({ candidates: [kept, fresh, dismissed] })).map(
        (item) => item.candidate_id,
      ),
    ).toEqual(["B1"]);
  });
});

describe("candidateActions", () => {
  it("还是 new 的三条路都能走：收下为下一轮输入 / 收下为材料 / 删掉", () => {
    expect(candidateActions(candidate())).toEqual(["keep-seed", "keep-material", "dismiss"]);
  });

  it("已经收下或删掉的没有可用动作（后端也会拒，界面先不画按钮）", () => {
    expect(candidateActions(candidate({ state: "kept", kept_as: "seed" }))).toEqual([]);
    expect(candidateActions(candidate({ state: "dismissed" }))).toEqual([]);
  });
});

describe("seedText", () => {
  it("假设：原文写进下一轮输入（不加戏）", () => {
    expect(seedText(candidate())).toBe("导出要支持按状态筛选");
  });

  it("盲区：写成一句「需要搞清楚」的话，带上下一步", () => {
    expect(
      seedText(
        candidate({
          kind: "blind_spot",
          statement: "并发导出时任务会不会互相覆盖",
          next_step: "问清导出任务是不是串行跑",
        }),
      ),
    ).toBe("需要搞清楚：并发导出时任务会不会互相覆盖（问清导出任务是不是串行跑）");
  });

  it("盲区没有下一步时也不编（只留前半句）", () => {
    expect(seedText(candidate({ kind: "blind_spot", statement: "权限边界没定", next_step: null }))).toBe(
      "需要搞清楚：权限边界没定",
    );
  });
});

describe("isRunning", () => {
  it("有一趟在跑就是真的（发起按钮要禁用）", () => {
    expect(isRunning([exploration(), exploration({ status: "running" })])).toBe(true);
    expect(isRunning([exploration(), exploration({ status: "failed" })])).toBe(false);
    expect(isRunning([])).toBe(false);
  });
});

describe("failureText", () => {
  it("有原因照实说", () => {
    expect(failureText(exploration({ status: "failed", failure_reason: "模型调用超时" }))).toBe(
      "模型调用超时",
    );
  });

  it("原因缺失也不留空", () => {
    expect(failureText(exploration({ status: "failed", failure_reason: null }))).toBe(
      "这趟调研失败了（后端没留下原因）",
    );
  });
});

describe("overflowNote", () => {
  it("没超限就什么都不说", () => {
    expect(overflowNote(exploration())).toBeNull();
  });

  it("超限时照实报截断了几条", () => {
    expect(overflowNote(exploration({ overflow_count: 3 }))).toBe("超出上限，已截断 3 条");
  });
});
