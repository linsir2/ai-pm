/* 出处索引：只建"这一轮里看得见"的编号。
    跨轮编号要等后端给出查询端点（计划 §10.3 的 P4 项），这里查不到就说查不到。

    三类来源：用户主张（claim）、席位判断（opinion）、材料块（material）。
    `kindLabel` 是链上那个"从哪来"的词（用户主张 / 席位名 / 材料名）。 */

import type { RoundView } from "./types";

export type EvidenceKind = "claim" | "opinion" | "material";

export interface EvidenceHit {
  eid: string;
  kind: EvidenceKind;
  /** 链上显示的来源词：用户主张 / 席位名 / 材料名。 */
  kindLabel: string;
  text: string;
  source: string;
}

export function buildEvidenceIndex(round: RoundView | null): Map<string, EvidenceHit> {
  const index = new Map<string, EvidenceHit>();
  if (round === null) return index;

  for (const claim of round.claims) {
    index.set(claim.eid, {
      eid: claim.eid,
      kind: "claim",
      kindLabel: "用户主张",
      text: claim.text,
      source: `你说的 · 原话「${truncate(claim.raw_span)}」`,
    });
  }
  for (const packet of round.seat_packets) {
    for (const opinion of packet.opinions) {
      if (opinion.eid === null) continue;
      index.set(opinion.eid, {
        eid: opinion.eid,
        kind: "opinion",
        kindLabel: packet.title,
        text: opinion.claim,
        source: `${packet.title} 的判断`,
      });
    }
  }
  for (const material of round.materials ?? []) {
    for (const chunk of material.chunks) {
      // 编号在整个出处账本里唯一，主张/判断先到先得，材料块只补没有的
      if (index.has(chunk.eid)) continue;
      index.set(chunk.eid, {
        eid: chunk.eid,
        kind: "material",
        kindLabel: material.title,
        text: chunk.text,
        source: `材料「${material.title}」· ${chunk.anchor}`,
      });
    }
  }
  return index;
}

export function truncate(text: string, limit = 42): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length <= limit ? clean : `${clean.slice(0, limit)}…`;
}

/** 这个编号落在哪份材料里（证据链上那个"上传材料.pdf"）；查不到就是 null。 */
export function materialTitle(eid: string, round: RoundView | null): string | null {
  if (round === null) return null;
  for (const material of round.materials ?? []) {
    if (material.chunks.some((chunk) => chunk.eid === eid)) return material.title;
  }
  return null;
}
