/* 检索投影的**纯函数**：哪一趟的 trace、那一行怎么写、材料是哪来的。
   组件只渲染，不做匹配/文案判断（`06` §3.2 硬规矩 3）。 */

import type { RetrievalView, RoundView, SeatPacketView } from "../api/types";

export function retrievalForStage(
  round: RoundView,
  stage: string,
  passIndex: number,
): RetrievalView | null {
  return (
    (round.retrievals ?? []).find(
      (trace) => trace.stage === stage && trace.pass_index === passIndex,
    ) ?? null
  );
}

/** 某个席位包所属那一趟的检索（同一趟的所有席位共享同一条）。 */
export function retrievalForPacket(
  round: RoundView,
  packet: SeatPacketView,
): RetrievalView | null {
  return retrievalForStage(round, "seat", packet.pass_index);
}

/**
 * 整合那一步的检索：成功取 `integration_seq`；失败取本次 pending 的
 * `integration_seq + 1`。没有本次 trace 就返回 null，不拿上一次成功冒充。
 */
export function retrievalForIntegration(round: RoundView): RetrievalView | null {
  const seq = round.integration_seq ?? 0;
  if (round.step !== "failed") {
    return retrievalForStage(round, "integrate", seq);
  }
  return retrievalForStage(round, "integrate", seq + 1);
}

export function retrievalLine(trace: RetrievalView): string {
  if (!trace.ok) {
    return `检索失败：${trace.error ?? "未知原因"}`;
  }
  return `命中 ${trace.hit_count} 块 / 带进 ${trace.selected_count} 块`;
}

/**
 * 材料来源：手勾 / 自动。
 *
 * **没有"手动"**：`AttachMaterials` 一定同时 pin 材料，所以"手动带料"永远是
 * "手勾"的子集；再分一档只会得到一个生产里不可达的分支。
 *
 * **老轮次不猜**：没有 `pinned_materials` 也没有 `retrievals` 时返回 `null`
 * （界面不画徽标）——把"未知"翻译成"自动"是撒谎。
 */
export function materialSource(
  materialId: string,
  pinnedMaterials: string[],
  hasRetrievals: boolean,
  origin = "",
): "手勾" | "自动" | null {
  // 联网结果走另一条路（一条结果一个号），既不是"手勾"也不是"自动"。
  if (origin === "web_search") return null;
  if (pinnedMaterials.includes(materialId)) return "手勾";
  return hasRetrievals ? "自动" : null;
}
