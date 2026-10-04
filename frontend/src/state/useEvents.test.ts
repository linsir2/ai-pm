/* SSE 契约：事件名、后端发什么、前端读什么。

**为什么需要这条**：前端只通过三个面接后端——30 个端点 JSON、4 个事件名 + payload 键、
6 个请求体。这一层**以前零测试**，而后端改事件名或 payload 键的后果是
"SSE 静默死亡但顶栏显示已连上"（`TopBar.tsx` 只看连接状态，不看有没有事件进来）。

**这条测试的局限（写清楚，别当它是全量契约测试）**：它把期望值**硬编码**在前端，
所以它钉的是"前端认为后端长这样"。后端改了而前端没跟上，这条会红（好）；
但前端改了而后端没跟上，这条不会红（那需要后端 DTO 与 TS 类型对齐的契约测试，
本仓没有 openapi 生成——`api/types.ts:1-2` 自己承认是手写的）。
*/

import { describe, expect, it } from "vitest";

/** 与 `useEvents.ts` 的 `EVENT_NAMES` 保持一致——改一处必须改另一处。 */
const EVENT_NAMES = [
  "round.updated",
  "packet.emitted",
  "doc.changed",
  "generation.failed",
] as const;

/** 后端**发**的键（`use_cases.py` 的四个 publish 点）。 */
const SENT_KEYS: Record<string, string[]> = {
  "packet.emitted": [
    "packet_id",
    "run_id",
    "kind",
    "produced_by",
    "basis",
    "seat_id",
    "pass_index",
    "opinions",
  ],
  "round.updated": ["step", "waiting_on", "seat_round_count"],
  "doc.changed": ["version_no", "written_block_ids", "summary"],
  "generation.failed": ["reason", "at_step"],
};

/** 前端 `workspace.ts` **读**的键（按事件分）。 */
const READ_KEYS: Record<string, string[]> = {
  "packet.emitted": ["seat_id", "opinions", "pass_index", "basis", "packet_id"],
  "round.updated": ["step", "waiting_on", "seat_round_count"],
  "doc.changed": ["written_block_ids"],
  "generation.failed": ["reason"],
};

describe("SSE 事件名恰好四个", () => {
  it("多一个少一个都不行——多出来的那个前端不会订阅，少掉的那个界面不更新", () => {
    expect([...EVENT_NAMES]).toEqual([
      "round.updated",
      "packet.emitted",
      "doc.changed",
      "generation.failed",
    ]);
  });

  it("每个事件都有前端在读的键（否则这个事件对界面没用）", () => {
    for (const name of EVENT_NAMES) {
      expect(READ_KEYS[name], name).not.toEqual([]);
    }
  });
});

describe("前端读的键必须是后端发的键的子集", () => {
  it("不许读后端没发的键（那会静默变 undefined）", () => {
    for (const name of EVENT_NAMES) {
      const sent = SENT_KEYS[name] ?? [];
      const read = READ_KEYS[name] ?? [];
      const unknown = read.filter((key) => !sent.includes(key));
      expect(unknown, `${name} 读了后端没发的键`).toEqual([]);
    }
  });

  it("packet.emitted 必须发 packet_id（留痕按它精确挂载）", () => {
    expect(SENT_KEYS["packet.emitted"]).toContain("packet_id");
    expect(READ_KEYS["packet.emitted"]).toContain("packet_id");
  });

  it("信封身份四件套后端都要发（§4.4 的通信形状）", () => {
    expect(SENT_KEYS["packet.emitted"]).toEqual(
      expect.arrayContaining(["packet_id", "run_id", "kind", "produced_by"]),
    );
  });
});
