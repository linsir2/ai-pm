/* 出处索引：只建"这一轮里看得见"的编号。
   跨轮编号要等后端给出查询端点（计划 §10.3 的 P4 项），这里查不到就说查不到。 */

import type { RoundView } from "./types";

export type EvidenceKind = "claim" | "opinion";

export interface EvidenceHit {
  eid: string;
  kind: EvidenceKind;
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
        text: opinion.claim,
        source: `${packet.title} 的判断`,
      });
    }
  }
  return index;
}

export function truncate(text: string, limit = 42): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length <= limit ? clean : `${clean.slice(0, limit)}…`;
}
