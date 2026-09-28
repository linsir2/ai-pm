/* 版本抽屉要用的纯函数：文案、比较对、计数都在这里算，组件只管画（所以能单测）。 */

import type { DiffView, VersionSummaryView } from "../api/types";

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function versionLabel(version: VersionSummaryView): string {
  return `v${version.version_no} · ${version.trigger_label} · ${formatTime(version.created_at)}`;
}

/** 点一个版本：已经在比较对里就不动，否则它当 `from`，`to` 不动（永远保持 from < to）。 */
export function pickPair(
  pair: [number, number] | null,
  versionNo: number,
  current: number,
): [number, number] {
  if (pair === null) return [versionNo, current];
  if (pair.includes(versionNo)) return pair;
  const to = pair[1];
  return versionNo < to ? [versionNo, to] : [to, versionNo];
}

export interface DiffCounts {
  added: number;
  removed: number;
  changed: number;
}

export function diffCounts(diff: DiffView): DiffCounts {
  return diff.groups.reduce<DiffCounts>(
    (acc, group) => ({
      added: acc.added + group.added.length,
      removed: acc.removed + group.removed.length,
      changed: acc.changed + group.changed.length,
    }),
    { added: 0, removed: 0, changed: 0 },
  );
}

export function diffHeadline(diff: DiffView): string {
  const counts = diffCounts(diff);
  if (counts.added + counts.removed + counts.changed === 0) {
    return `v${diff.from_version_no} 和 v${diff.to_version_no} 内容一样`;
  }
  return (
    `v${diff.from_version_no} → v${diff.to_version_no}：` +
    `新增 ${counts.added} 条 · 改写 ${counts.changed} 条 · 删除 ${counts.removed} 条`
  );
}
