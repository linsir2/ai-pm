/* 手改文档的能力：改 / 加 / 删一条。

   做成 context 是因为条目散在四个渲染点上（普通章节、功能字段、关键字段网格、偏好区），
   一路传回调会穿过 BlockTree / FeatureBlock 好几层。

   两个规矩来自设计：**手改是我们唯一允许的"人写文档"路径**（AI 仍然只能出草案），
   以及**改过的块 AI 之后动不了**（后端 `BlockManuallyEdited` 拦着）——界面只负责把入口给到。 */

import { createContext, useContext } from "react";
import type { ReactNode } from "react";

export interface EditApi {
  /** 现在能不能改（有项目、没有别的请求在跑）。 */
  canEdit: boolean;
  busy: boolean;
  edit: (blockId: string, entryId: string, text: string) => void;
  add: (blockId: string, text: string) => void;
  remove: (blockId: string, entryId: string) => void;
}

const NO_EDIT: EditApi = {
  canEdit: false,
  busy: false,
  edit: () => undefined,
  add: () => undefined,
  remove: () => undefined,
};

const EditContext = createContext<EditApi>(NO_EDIT);

export function EditScope({ value, children }: { value: EditApi; children: ReactNode }) {
  return <EditContext.Provider value={value}>{children}</EditContext.Provider>;
}

export function useEdit(): EditApi {
  return useContext(EditContext);
}
