/* 留痕的摆法：哪一条挂在哪个席位包下面（同席位 + 同一趟才是一件事）。 */

import type { DroppedView, SeatPacketView } from "../api/types";

export function dropsFor(packet: SeatPacketView, dropped: DroppedView[]): DroppedView[] {
  return dropped.filter(
    (item) => item.seat_id === packet.seat_id && item.pass_index === packet.pass_index,
  );
}

/** 挂不到当前这趟席位包上的留痕：更早那趟的（闭环区只显示本趟席位包）、
    地址写歪的、归因对不上号的。闭环区末尾单独一行摆出来，不许静默消失。 */
export function unplacedDrops(
  dropped: DroppedView[],
  packets: SeatPacketView[],
): DroppedView[] {
  return dropped.filter(
    (item) =>
      !packets.some(
        (packet) =>
          packet.seat_id === item.seat_id && packet.pass_index === item.pass_index,
      ),
  );
}

export function dropHeadline(packet: SeatPacketView, drops: DroppedView[]): string {
  if (drops.length === 0) return "";
  return `它说的 ${packet.opinions.length} 条里，${drops.length} 条没进卡片`;
}
