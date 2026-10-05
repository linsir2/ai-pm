/* 讨论区的判断（M9）：纯函数。组件只负责把这里的结果画出来。
   数据一律来自后端的 `ThreadView` 投影，这里不拼、不猜。 */

import type { CardView, QuestionView, SchemaView, ThreadView } from "../api/types";

/**
 * 一条线程最多几条轮次。**离线写死**：后端 `domain/discussion/thread.py` 的
 * `MAX_DISCUSSION_TURNS`（M9-T1）没有端点露出来——同 `state/schema.ts`
 * `FALLBACK_LIMITS` 的做法：后端不变时它就是同一份值。改这里之前先改那边。
 */
export const DISCUSSION_TURN_LIMIT = 12;

/** 开聊卡上的一个固定角色（M9-T2）。真相源是后端
 *  `infrastructure/discussion/roles.toml`，随 `GET /schema` 的 `discussion_roles` 下来。 */
export interface DiscussionRoleOption {
  role_id: string;
  title: string;
  focus: string;
}

/**
 * 拿不到 `/schema` 时的离线回落：**只有默认那一个**。
 *
 * 为什么不是三条：`role_id` 是后端必填项（`StartDiscussion` 认 `dlg.*` 与 `seat.*`），
 * 回落空了连 `resolveRoleId` 的默认值都发不出去；抄齐三条才是第二个真相源
 * （后端改了角色表，界面还摆着旧的）。
 */
const FALLBACK_ROLES: DiscussionRoleOption[] = [
  { role_id: "dlg.challenger", title: "反方", focus: "找漏洞、要反例、泼冷水" },
];

/** `/schema` 里那份角色表 → 开聊卡的选项（照抄，不排序不补）。 */
export function roleOptions(schema: SchemaView | null): DiscussionRoleOption[] {
  return schema?.discussion_roles ?? FALLBACK_ROLES;
}

/** 没指定时的默认角色：反方（`dlg.challenger`，与后端 roles.toml 第一条同序）。 */
export const DEFAULT_ROLE_ID = "dlg.challenger";

/**
 * 开聊要请谁（`resolveRoleId`）：
 * 1. 显式给的赢（问卷里"谁问的"不抢）；
 * 2. 没说就用问卷题的 `asked_by[0]`——**席位 id 照收**，后端认 `seat.*`；
 * 3. 两边都没有就默认反方。
 */
export function resolveRoleId(
  explicit?: string | null,
  question?: QuestionView | null,
): string {
  if (explicit !== undefined && explicit !== null && explicit.trim() !== "") {
    return explicit;
  }
  const asked = question?.asked_by[0];
  if (asked !== undefined && asked.trim() !== "") return asked;
  return DEFAULT_ROLE_ID;
}

/** 角色 id → 开聊卡上的那条；不在表里就**照实显示原值**（不猜中文名）。
 *
 *  问卷入口带来的 `seat.*` 席位 id 就是这条路径——那张表在 `available_seats` 里，
 *  这里不重复抄一份；`MaterialPicker` 同一口径（认不出就显示 id 本身）。 */
export function roleOptionOf(
  roleId: string,
  options: DiscussionRoleOption[],
): DiscussionRoleOption {
  return (
    options.find((item) => item.role_id === roleId) ?? {
      role_id: roleId,
      title: roleId,
      focus: "",
    }
  );
}

/** 开聊卡上的几选一：`/schema` 那几行；锚点带的是**表外的 id**（问卷里的席位）
 *  时多一条并排在最前——问卷入口的 UX：先看到刚问你的那个人。 */
export function roleChoices(
  anchorRoleId: string | null,
  options: DiscussionRoleOption[],
): DiscussionRoleOption[] {
  const anchor = anchorRoleId?.trim() ?? "";
  if (anchor === "" || options.some((item) => item.role_id === anchor)) {
    return [...options];
  }
  return [roleOptionOf(anchor, options), ...options];
}

/** 开聊的锚点：问卷入口带题面与"问这题的人"，讨论 tab 自由开聊时是用户自己打的字。 */
export interface DiscussionAnchor {
  topic: string;
  /** 问这题的那个 id（`seat.*` 或 `dlg.*`）；null = 交给默认（反方）。 */
  role_id: string | null;
}

/**
 * 这张卡现在能不能收下：**只看还没收（`kept_ref` 空）**。
 *
 * **不看线程开没开**：后端 `thread.py::keep_card` 收下是回流动作，判据只读 `turns`
 * （只读的是 turns，不是 status）——关掉的线程照样能收下那张卡。这里藏掉按钮，
 * 后端那个分支就没有消费者了。
 */
export function cardKeepable(card: CardView): boolean {
  return card.kept_ref === null;
}

/**
 * 能不能再追问一轮：**与后端同口径**，否则会点亮一个必然被拒的按钮。
 *
 * 后端 `thread.say_pair` 的门是 `len(turns) + 2 > 12` 才拒（`thread.py:149`）——
 * 一说就是一**对**（user＋assistant 两条），所以 10 轮是最后一说，11 轮就塞不下了。
 * 计划里原写的 `turns < 12` 是错的（会亮一个必然 409 的按钮），已按 T4 实码改正。
 */
export function canDiscuss(thread: ThreadView): boolean {
  return thread.status === "open" && thread.turns.length + 2 <= DISCUSSION_TURN_LIMIT;
}

/** 还剩几轮可说（界面显示用；0 就是聊满了）。 */
export function turnsLeft(thread: ThreadView): number {
  if (thread.status !== "open") return 0;
  return Math.max(0, Math.floor((DISCUSSION_TURN_LIMIT - thread.turns.length) / 2));
}

/**
 * 能不能「总结成卡」：
 * - 关掉的线程：后端 `AssembleCards` 直接 `ThreadClosed`（409）——不给入口；
 * - 还没说过话的线程：**后端不拦**（它照发一次模型调用），但总结一段空对话没有
 *   对象，界面不点亮这个按钮（省一次白烧的调用）；用户想说就先去说。
 */
export function canAssemble(thread: ThreadView): boolean {
  return thread.status === "open" && thread.turns.length > 0;
}

const STATUS_TEXT: Record<string, string> = {
  open: "聊着呢",
  closed: "已关掉",
};

export function threadStatusText(status: string): string {
  return STATUS_TEXT[status] ?? status;
}
