/* 留痕的摆法：哪一条挂在哪个席位包下面。

**有 `packet_id` 就按 id 精确匹配**——它是全局唯一的（后端槽位保证），
不再靠 `(seat_id, pass_index)` 猜。那个启发式有个真问题：`pass_index` 只在**同一轮内**
区分趟次，而 `Round.dropped` 是**跨轮累积**的——同一席位在两轮里都有 `pass_index=1`
的留痕时，启发式会把它们混在一起。

**没有 `packet_id` 就回退启发式**：老数据（这个字段是后加的）、assembly 归因对不上号、
tool 失败——它们本来就没有 agent 包，`packet_id` 是 `undefined`。
*/

import type { DroppedView, SeatPacketView } from "../api/types";

/** 这条留痕属于这个席位包吗？两侧都有 id 才精确，否则按 (席位, 趟)。 */
function belongsTo(item: DroppedView, packet: SeatPacketView): boolean {
  if (item.packet_id && packet.packet_id) {
    return item.packet_id === packet.packet_id;
  }
  return item.seat_id === packet.seat_id && item.pass_index === packet.pass_index;
}

export function dropsFor(packet: SeatPacketView, dropped: DroppedView[]): DroppedView[] {
  return dropped.filter((item) => belongsTo(item, packet));
}

/** 挂不到当前这趟席位包上的留痕：更早那趟的（闭环区只显示本趟席位包）、
    地址写歪的、归因对不上号的。闭环区末尾单独一行摆出来，不许静默消失。 */
export function unplacedDrops(
  dropped: DroppedView[],
  packets: SeatPacketView[],
): DroppedView[] {
  return dropped.filter((item) => !packets.some((packet) => belongsTo(item, packet)));
}

/** 工具这一趟留下的（检索失败 / 没搜到结果 / 预算没带进去）。

    它们与席位意见无关（`seat_id` 恒为 null），所以不许混进"去处写错的"那一句里——
    闭环区单列一段，说清是**联网这一步**的事。 */
export function toolDrops(dropped: DroppedView[]): DroppedView[] {
  return dropped.filter((item) => item.source === "tool");
}

export function dropHeadline(packet: SeatPacketView, drops: DroppedView[]): string {
  if (drops.length === 0) return "";
  return `它说的 ${packet.opinions.length} 条里，${drops.length} 条没进卡片`;
}
