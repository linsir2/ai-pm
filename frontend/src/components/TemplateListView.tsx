/* 模板库 A 屏（`06` §2.3）：一行一条 + 自建那份可编辑可删。
 *
 * **不做卡片墙 / 分类树 / 搜索 / 展开预览**：只有两个内置模板时，这些控件全部的工作
 * 量都在处理"空结果"（Storybook 有 19 种框架模板时把选择器整个砍掉了）。预览也不另做
 * 一屏——编辑器本身就是预览。
 *
 * **这一屏一个业务判断都不做**（`App.tsx` 的既有纪律）：内置还是自建由后端的
 * `builtin` 说了算，组件不自己猜。删除用内联二次确认（本仓没有确认弹窗的既有模式，
 * 不为它造一个）。 */

import { useState } from "react";

import type { TemplateView } from "../api/types";
import { Banner } from "./States";

export function TemplateListView({
  templates,
  onUse,
  onEdit,
  onDelete,
  onNew,
  error,
  pending = false,
}: {
  templates: TemplateView[];
  onUse: (templateId: string) => void;
  onEdit: (templateId: string) => void;
  onDelete: (templateId: string) => void;
  onNew: () => void;
  error: string | null;
  pending?: boolean;
}) {
  /** 正在二次确认的那一行：再点一次同一个按钮才真删。 */
  const [confirming, setConfirming] = useState<string | null>(null);

  return (
    <div className="stack" style={{ gap: "var(--space-3)" }}>
      {error !== null && <Banner>{error}</Banner>}
      <ul className="stack" style={{ gap: "var(--space-1)", listStyle: "none", margin: 0, padding: 0 }}>
        {templates.map((item) => {
          const builtIn = item.builtin === true;
          const confirmingThis = confirming === item.template_id;
          return (
            <li key={item.template_id} className="row" style={{ justifyContent: "space-between" }}>
              <span className="row">
                <strong>{item.title}</strong>
                <span className="muted small">
                  {item.sections.length} 章 · {builtIn ? "内置" : "我的"}
                </span>
              </span>
              <span className="row">
                <button
                  className="btn btn--ghost"
                  type="button"
                  onClick={() => onUse(item.template_id)}
                >
                  用这个建项目
                </button>
                {!builtIn && (
                  <>
                    <button
                      className="btn btn--ghost"
                      type="button"
                      onClick={() => onEdit(item.template_id)}
                    >
                      编辑
                    </button>
                    <button
                      className="btn btn--ghost"
                      type="button"
                      disabled={pending && !confirmingThis}
                      onClick={() => {
                        if (confirmingThis) {
                          setConfirming(null);
                          onDelete(item.template_id);
                        } else {
                          setConfirming(item.template_id);
                        }
                      }}
                    >
                      {confirmingThis ? "确认删除？" : "删除"}
                    </button>
                  </>
                )}
              </span>
            </li>
          );
        })}
      </ul>
      <div className="row">
        <button className="btn btn--primary" type="button" onClick={onNew}>
          新建模板
        </button>
        <span className="muted small">
          删掉只是不能再用它建新项目，已建的项目不受影响。
        </span>
      </div>
    </div>
  );
}