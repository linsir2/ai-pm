/* 选区把手的定位数学（M13）：纯函数。DOM 侧只负责把 rect 喂进来，
   判断（居中 / 翻转 / clamp）全在这里，测得着。 */

export interface HandleRect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export interface HandleSize {
  width: number;
  height: number;
}

/**
 * 选区 → 把手坐标（相对 `.doc__inner`，把手绝对定位在栏内）。
 *
 * - 横向：以选区中线居中，clamp 在文档栏内（永不越到右栏）；
 * - 纵向：默认贴在选区上方 8px；上方放不下（顶出栏顶）就翻到下方 8px。
 */
export function handlePlacement(
  selection: HandleRect,
  column: HandleRect,
  size: HandleSize,
): { left: number; top: number } {
  const center = (selection.left + selection.right) / 2;
  const maxLeft = Math.max(column.right - size.width, column.left);
  const left = Math.min(Math.max(center - size.width / 2, column.left), maxLeft);
  const above = selection.top - 8 - size.height;
  const top = above >= column.top ? above : selection.bottom + 8;
  return { left, top };
}
