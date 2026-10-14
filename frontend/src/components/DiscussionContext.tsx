/* 选段进对话的能力（M13/M17）：选区浮条用它，免得一路传 props。
   点「添加到对话」= 把主题设成锚点 + 切到讨论页签；真开聊是用户在开聊卡上点「开聊」之后的事
   （入口与动作分开，与 `ExplorationContext` 同一条纪律）。 */

import { createContext, useContext } from "react";
import type { ReactNode } from "react";

export interface DiscussionApi {
  /** 现在能不能发起（有项目、且没有别的动作在跑）。 */
  canStart: boolean;
  /** 传**原文**即可：归一化与超长截断在实现处（`discussionTopic`）做一次。 */
  start: (text: string) => void;
}

const NO_DISCUSSION: DiscussionApi = {
  canStart: false,
  start: () => undefined,
};

const DiscussionContext = createContext<DiscussionApi>(NO_DISCUSSION);

export function DiscussionScope({
  value,
  children,
}: {
  value: DiscussionApi;
  children: ReactNode;
}) {
  return <DiscussionContext.Provider value={value}>{children}</DiscussionContext.Provider>;
}

export function useDiscussion(): DiscussionApi {
  return useContext(DiscussionContext);
}
