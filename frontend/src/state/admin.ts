/* 管理后台的**判断**都住在这里（前端不变式：组件不做业务判断，`06` §3.2 第 5 条）。
   `AdminView` 只调用这些纯函数取形状、渲染——不内联 filter / 三元 / `??`。

   解封：后端没有独立 unban 端点，`POST /auth/approve` 对任意状态置 active（用例不做状态机
   限制），所以 `actionFor` 把 banned 判成 `kind: "unban"`，由组件把 unban 映射到 approve
   ——真实生效，不是假实现。若后端日后给 approve 加状态门，这里改一处。 */

import type { AuthUser, UsageRow } from "../api/client";

/** 待审批的用户（`status === "pending"`），保持传入顺序。 */
export function pendingUsers(users: readonly AuthUser[]): AuthUser[] {
  return users.filter((entry) => entry.status === "pending");
}

/** 用户行上的动作：`ban` = 封禁，`unban` = 解封（复用 approve）。 */
export type AdminActionKind = "ban" | "unban";

export interface AdminAction {
  kind: AdminActionKind;
  label: string;
}

/** 这个用户该显示封禁还是解封。banned → 解封；其余（active / pending / rejected）→ 封禁。 */
export function actionFor(user: AuthUser): AdminAction {
  if (user.status === "banned") return { kind: "unban", label: "解封" };
  return { kind: "ban", label: "封禁" };
}

/** 某个用户的用量；用量列表里没有他就算 0（缺省逻辑不落在组件里）。 */
export function usageOf(userId: string, usage: readonly UsageRow[]): number {
  return usage.find((row) => row.user_id === userId)?.calls ?? 0;
}
