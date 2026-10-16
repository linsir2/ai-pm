/* 管理后台的纯函数判断（前端不变式：组件不做业务判断，判据在这类纯模块里）。
   与 `document/status.test.ts` 同一体例：无网络、无模型、无 React。 */

import { describe, expect, it } from "vitest";

import type { AuthUser, UsageRow } from "../api/client";
import { actionFor, pendingUsers, usageOf } from "./admin";

function user(id: string, status: string, role = "user"): AuthUser {
  return { user_id: id, username: id, role, status };
}

describe("pendingUsers", () => {
  it("只挑 status === pending 的，保持原顺序", () => {
    const users = [
      user("a", "pending"),
      user("b", "active"),
      user("c", "banned"),
      user("d", "pending"),
    ];

    expect(pendingUsers(users).map((entry) => entry.user_id)).toEqual(["a", "d"]);
  });

  it("一个待批都没有就空数组", () => {
    expect(pendingUsers([user("a", "active")])).toEqual([]);
    expect(pendingUsers([])).toEqual([]);
  });
});

describe("actionFor", () => {
  it("banned → 解封（复用 approve 端点）", () => {
    expect(actionFor(user("a", "banned"))).toEqual({ kind: "unban", label: "解封" });
  });

  it("非 banned → 封禁（含 active / pending / rejected）", () => {
    expect(actionFor(user("a", "active"))).toEqual({ kind: "ban", label: "封禁" });
    expect(actionFor(user("b", "pending"))).toEqual({ kind: "ban", label: "封禁" });
    expect(actionFor(user("c", "rejected"))).toEqual({ kind: "ban", label: "封禁" });
  });
});

describe("usageOf", () => {
  const usage: UsageRow[] = [
    { user_id: "a", username: "a", calls: 7 },
    { user_id: "b", username: "b", calls: 0 },
  ];

  it("取到就回它的 calls（含 0）", () => {
    expect(usageOf("a", usage)).toBe(7);
    expect(usageOf("b", usage)).toBe(0);
  });

  it("列表里没有这个用户 → 0（缺省逻辑不落在组件里）", () => {
    expect(usageOf("z", usage)).toBe(0);
    expect(usageOf("z", [])).toBe(0);
  });
});
