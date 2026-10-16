/* 模板库 A 屏（`06` §2.3）：一行一条 + 自建那份可编辑可删 + **可展开的章节预览**。
 *
 * **仍然不做**：卡片墙 / 分类树 / 搜索。这三条的原判据是"只有两个内置模板时，
 * 这些控件的工作量全在处理空结果"——那一条依然成立，展开不属于这一类。
 *
 * **本次加的是"展开看章节"**（`ui-patterns` 的 TemplateGallery：*Give every template
 * a visual preview… a name helps, but a thumbnail of the finished result helps more*）。
 * 之前这里只写"13 章 · 内置"，用户选之前完全不知道这套模板是什么。展开不是新开一屏
 * ——编辑器本来就在这一屏里，纯列表态展开更轻。
 *
 * **选中不靠 hover**：点一下才展开预览。WCAG 3.2.1 要求获得焦点不得引起上下文变化，
 * 而 hover/焦点触发的预览在键盘与触屏上都不成立（WordPress FSE 讨论里选了同样结论）。
 *
 * **这一屏一个业务判断都不做**（`App.tsx` 的既有纪律）：内置还是自建由后端的
 * `builtin` 说了算，组件不自己猜。删除用内联二次确认（本仓没有确认弹窗的既有模式，
 * 不为它造一个）。 */

import { useState } from "react";

import { ROLE_TEXT } from "../labels";
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
  /** 正在展开章节预览的那一条。同一时刻只展开一条——两份清单并排没有对比价值。 */
  const [previewing, setPreviewing] = useState<string | null>(null);

  return (
    <div className="stack" style={{ gap: "var(--space-4)" }}>
      <div className="tlib__head">
        <h2 className="home__title">模板库</h2>
        <p className="home__lede">
          模板是一套章节骨架——决定这个项目文档分成哪几章、每章问什么。
          建项目时选一份，之后章节内容可以在文档里手改。
        </p>
      </div>

      {error !== null && <Banner>{error}</Banner>}

      {/* 内置那份不给编辑/删除按钮，但**说清为什么**——不给理由的禁用只会让人
          以为是坏了。整库只说明一次：两份一样的说明就是噪音。
          判断是数据判断（有没有 builtin），不是业务判断。 */}
      {templates.some((item) => item.builtin === true) && (
        <p className="tlib__why">
          内置模板随产品发布，改动会让已有项目的章节对不上，所以不能改也不能删。要改就自己建一份。
        </p>
      )}

      <ul className="stack" style={{ gap: "var(--space-2)", listStyle: "none", margin: 0, padding: 0 }}>
        {templates.map((item) => {
          const builtIn = item.builtin === true;
          const confirmingThis = confirming === item.template_id;
          const previewingThis = previewing === item.template_id;
          return (
            <li key={item.template_id} className="tlib__item">
              <div className="tlib__row">
                <span className="tlib__name">
                  <strong>{item.title}</strong>
                  <span className="muted small">
                    {item.sections.length} 章 · {builtIn ? "内置" : "我的"}
                  </span>
                </span>

                <span className="tlib__acts">
                  <button
                    className="btn btn--ghost btn--sm"
                    type="button"
                    aria-expanded={previewingThis}
                    onClick={() => setPreviewing(previewingThis ? null : item.template_id)}
                  >
                    {previewingThis ? "收起" : "有哪几章"}
                  </button>
                  <button
                    className="btn btn--ghost btn--sm"
                    type="button"
                    onClick={() => onUse(item.template_id)}
                  >
                    用这个建项目
                  </button>
                  {!builtIn && (
                    <>
                      <button
                        className="btn btn--ghost btn--sm"
                        type="button"
                        onClick={() => onEdit(item.template_id)}
                      >
                        编辑
                      </button>
                      <button
                        className="btn btn--ghost btn--sm"
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
              </div>

              {previewingThis && (
                <ol className="tlib__preview">
                  {item.sections.map((section) => (
                    <li key={section.section_id}>
                      <span className="tlib__sec-id mono">{section.section_id}</span>
                      {/* 标题会被 tag 挤到截断，完整名字留在 title 里 */}
                      <span className="tlib__sec-title" title={section.title}>
                        {section.title}
                      </span>
                      <span className="tag">{ROLE_TEXT[section.role] ?? section.role}</span>
                    </li>
                  ))}
                </ol>
              )}
            </li>
          );
        })}
      </ul>

      <div className="tlib__foot">
        <button className="btn" type="button" onClick={onNew}>
          新建模板
        </button>
        <span className="muted small">模板只能新建时用一次，之后章节内容可以在文档里手改。</span>
      </div>
    </div>
  );
}