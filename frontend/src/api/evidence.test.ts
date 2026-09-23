import { describe, expect, it } from "vitest";

import { buildEvidenceIndex, truncate } from "./evidence";
import type { RoundView } from "./types";

const round = {
  round_id: "r1",
  claims: [
    { eid: "E1", text: "运营每天都要用", raw_span: "运营每天都要用" },
    { eid: "E2", text: "数据量大概几万", raw_span: "数据量大概几万" },
  ],
  seat_packets: [
    {
      seat_id: "seat.data_scale",
      title: "数据规模",
      opinions: [
        { eid: "E5", claim: "几万行不必分页", because: "同量级做法" },
        { eid: null, claim: "还没拿到编号的判断" },
      ],
    },
  ],
} as unknown as RoundView;

describe("buildEvidenceIndex", () => {
  it("主张与已经拿到编号的判断都能查到", () => {
    const index = buildEvidenceIndex(round);
    expect(index.get("E1")?.kind).toBe("claim");
    expect(index.get("E1")?.source).toContain("运营每天都要用");
    expect(index.get("E5")?.kind).toBe("opinion");
    expect(index.get("E5")?.source).toBe("数据规模 的判断");
  });

  it("还没编号的判断不进索引（汇合点才发号）", () => {
    expect(buildEvidenceIndex(round).has("null")).toBe(false);
    expect(buildEvidenceIndex(round).size).toBe(3);
  });

  it("没有轮次就是空索引", () => {
    expect(buildEvidenceIndex(null).size).toBe(0);
  });
});

describe("truncate", () => {
  it("压掉换行并截断", () => {
    expect(truncate("a\n b", 10)).toBe("a b");
    expect(truncate("一二三四五", 3)).toBe("一二三…");
  });
});
