/* 出处胶囊 —— 产品唯一的信任机制（DESIGN.md R3）。不藏进 tooltip：
   看得见编号，点得回到原话。查不到的编号就老实说查不到。 */

import { truncate } from "../api/evidence";
import { useEvidence } from "./EvidenceContext";

export function EvidenceChip({ eids }: { eids: string[] }) {
  const { index, onJump } = useEvidence();
  if (eids.length === 0) return null;

  return (
    <span className="chips">
      {eids.map((eid) => {
        const hit = index.get(eid);
        const label =
          hit === undefined
            ? `出处 ${eid}（不在这一轮里，点一下去查它在哪一轮、被谁用着）`
            : `出处 ${eid}：${truncate(hit.text, 60)}`;
        return (
          <button
            key={eid}
            type="button"
            // 跨轮编号本地查不到，但后端查得到——所以不禁用，交给 onJump 去问
            className={hit === undefined ? "chip chip--remote" : "chip"}
            title={hit === undefined ? label : `${label}\n${hit.source}`}
            aria-label={label}
            onClick={() => onJump(eid)}
          >
            {eid}
          </button>
        );
      })}
    </span>
  );
}
