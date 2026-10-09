/* 证据链：这条内容从哪来 —— [来源] → [编号…] → [当前条目]。
   编号可点（走 EvidenceContext 的跳转）；链条查不全就老实显示"出处"，不编。
   同来源的编号并在一组里（"用户主张 E1 E2"），换来源才加箭头。 */

import type { ReactNode } from "react";

import { materialTitle } from "../api/evidence";
import type { RoundView } from "../api/types";
import { useEvidence } from "./EvidenceContext";

const MAX_EIDS = 4;

export function EvidenceChain({ refs, round }: { refs: string[]; round: RoundView | null }) {
  const { index, onJump } = useEvidence();
  if (refs.length === 0) return null;

  const shown = refs.slice(0, MAX_EIDS);
  const rest = refs.length - shown.length;
  const segments: ReactNode[] = [];
  let lastKind: string | null = null;

  shown.forEach((eid) => {
    const hit = index.get(eid);
    const kindLabel = hit?.kindLabel ?? "出处";
    if (kindLabel !== lastKind) {
      if (lastKind !== null) {
        segments.push(
          <span className="chain__arrow" aria-hidden="true" key={`arrow-${eid}`}>
            →
          </span>,
        );
      }
      segments.push(
        <span className="chain__kind" key={`kind-${eid}`}>
          {kindLabel}
        </span>,
      );
      lastKind = kindLabel;
    }
    segments.push(
      <button
        type="button"
        className="chain__eid mono"
        key={eid}
        title={hit === undefined ? `${eid}：不在这一轮里，点一下去查它落在哪一轮` : `${eid}：${hit.source}`}
        onClick={() => onJump(eid)}
      >
        {eid}
      </button>,
    );
    // 材料块自己的 kindLabel 就是材料名；主张/判断再找它落在哪份材料里
    const material = hit?.kind === "material" ? null : materialTitle(eid, round);
    if (material !== null) {
      segments.push(
        <span className="chain__material" key={`material-${eid}`}>
          {material}
        </span>,
      );
    }
  });

  if (rest > 0) {
    segments.push(
      <span className="chain__more" key="rest" title={`还有 ${rest} 个编号没展开`}>
        +{rest}
      </span>,
    );
  }
  segments.push(
    <span className="chain__arrow" aria-hidden="true" key="arrow-end">
      →
    </span>,
    <span className="chain__end" key="end">
      当前条目
    </span>,
  );

  return (
    <div className="chain">
      <span className="chain__label">证据链</span>
      <span className="chain__items">{segments}</span>
    </div>
  );
}
