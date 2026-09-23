/* 出处索引与跳转：文档区、闭环区、抽屉里都要用，所以走 context，
   免得把 index 一层层传过 BlockTree。 */

import { createContext, useContext } from "react";
import type { ReactNode } from "react";

import type { EvidenceHit } from "../api/evidence";

export interface EvidenceApi {
  index: Map<string, EvidenceHit>;
  onJump: (eid: string) => void;
}

const EMPTY: EvidenceApi = { index: new Map(), onJump: () => undefined };

const EvidenceContext = createContext<EvidenceApi>(EMPTY);

export function EvidenceScope({
  index,
  onJump,
  children,
}: {
  index: Map<string, EvidenceHit>;
  onJump: (eid: string) => void;
  children: ReactNode;
}) {
  return (
    <EvidenceContext.Provider value={{ index, onJump }}>{children}</EvidenceContext.Provider>
  );
}

export function useEvidence(): EvidenceApi {
  return useContext(EvidenceContext);
}
