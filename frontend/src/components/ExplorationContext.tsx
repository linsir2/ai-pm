/* 发起调研的能力（M8）：选区浮条与讨论区顶部用它，免得一路传 props。
   点「调研这段」/「发起调研」只是打开发起抽屉；真发请求是抽屉上「开始调研」之后的事
   （入口与动作分开）。 */

import { createContext, useContext } from "react";
import type { ReactNode } from "react";

import type { ExplorationAnchor } from "../api/payload";

export interface ResearchApi {
  /** 现在能不能发起（有项目、且没有别的动作在跑）。 */
  canStart: boolean;
  start: (anchor: ExplorationAnchor) => void;
}

const NO_RESEARCH: ResearchApi = {
  canStart: false,
  start: () => undefined,
};

const ExplorationContext = createContext<ResearchApi>(NO_RESEARCH);

export function ExplorationScope({
  value,
  children,
}: {
  value: ResearchApi;
  children: ReactNode;
}) {
  return <ExplorationContext.Provider value={value}>{children}</ExplorationContext.Provider>;
}

export function useExploration(): ResearchApi {
  return useContext(ExplorationContext);
}
