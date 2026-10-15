/* 模板编辑器 B 屏（`06` §2.3）：单栏，每章一块，元数据行内编辑。
 *
 * **为什么不用右侧属性面板**：6–13 项时"选中 → 切面板 → 改 → 切回"的往返成本高于收益
 * （json-editor 从三栏退化成单栏行内是实测记录）。
 *
 * **校验一律用界面结构表达，不报错红字**（结构性约束在整个编辑过程里天然被违反，红字会
 * 一直亮着）：锚点章的删除按钮置灰、已被占用的锚点 role 在下拉里置灰、锚点章不显示判据
 * 输入（后端本来就拒）、数字输入 `min=1`。**不加 dry-run 校验端点**——加了就是两条真相源。
 *
 * **组件不做业务判断**（`06` §3.2）：锚点是谁、新章 id 叫什么、能不能选，全问
 * `templates/draft.ts`。 */

import type { TemplateSectionView, TemplateView } from "../api/types";
import { blockKinds, templateRoles } from "../state/schema";
import { ROLE_TEXT } from "../labels";
import {
  addSection,
  canPickRole,
  isAnchor,
  moveSection,
} from "../templates/draft";
import { Banner } from "./States";

export function TemplateEditor({
  title,
  sections,
  baseOptions,
  onChange,
  onBack,
  onSave,
  error,
  pending = false,
}: {
  title: string;
  sections: readonly TemplateSectionView[];
  /** 复制源下拉的选项（另一份模板的可编辑副本，不含 builtin 那份自己）。 */
  baseOptions: readonly TemplateView[];
  onChange: (next: {
    title?: string;
    sections?: TemplateSectionView[];
  }) => void;
  onBack: () => void;
  onSave: () => void;
  error: string | null;
  pending?: boolean;
}) {
  const roles = templateRoles();
  const kinds = blockKinds();

  const patch = (index: number, changes: Partial<TemplateSectionView>) =>
    onChange({
      sections: sections.map((item, at) => (at === index ? { ...item, ...changes } : item)),
    });

  return (
    <div className="stack" style={{ gap: "var(--space-3)" }}>
      {error !== null && <Banner>{error}</Banner>}

      <header className="row" style={{ justifyContent: "space-between" }}>
        <button className="btn btn--ghost" type="button" onClick={onBack}>
          返回
        </button>
        <label className="row">
          <span className="muted small">基于</span>
          <select
            className="text-input"
            aria-label="复制源"
            value=""
            onChange={(event) => {
              const picked = event.target.value;
              if (picked !== "") {
                const base = baseOptions.find((item) => item.template_id === picked);
                if (base !== undefined) onChange({ sections: base.sections.map((s) => ({ ...s })) });
              }
            }}
          >
            <option value="">选一份复制…</option>
            {baseOptions.map((item) => (
              <option key={item.template_id} value={item.template_id}>
                {item.title}
              </option>
            ))}
          </select>
        </label>
        <label className="row">
          <span className="muted small">标题</span>
          <input
            className="text-input"
            aria-label="模板标题"
            value={title}
            onChange={(event) => onChange({ title: event.target.value })}
          />
        </label>
        <button className="btn btn--primary" type="button" disabled={pending} onClick={onSave}>
          {pending ? "正在存…" : "保存"}
        </button>
      </header>

      {sections.map((section, index) => {
        const anchor = isAnchor(section);
        const minEntries =
          typeof section.min_entries === "object" && section.min_entries !== null
            ? section.min_entries.count
            : typeof section.min_entries === "number"
              ? section.min_entries
              : null;
        return (
          <section key={section.section_id} className="stack" style={{ gap: "var(--space-1)" }}>
            <div className="row">
              <code className="mono muted small">{section.section_id}</code>
              <input
                className="text-input"
                aria-label={`${section.section_id} 标题`}
                value={section.title}
                onChange={(event) => patch(index, { title: event.target.value })}
              />
              <select
                className="text-input"
                aria-label={`${section.section_id} 角色`}
                value={section.role}
                onChange={(event) => patch(index, { role: event.target.value })}
              >
                {roles.map((role) => (
                  <option
                    key={role}
                    value={role}
                    disabled={!canPickRole(sections, role) && role !== section.role}
                  >
                    {ROLE_TEXT[role] ?? role}
                    {canPickRole(sections, role) ? "" : "（已占用）"}
                  </option>
                ))}
              </select>
              {anchor ? (
                <span className="muted small">锚点 · 判据由引擎算</span>
              ) : (
                <>
                  <label className="row">
                    <span className="muted small">至少</span>
                    <input
                      className="text-input"
                      type="number"
                      min={1}
                      aria-label={`${section.section_id} 至少几条`}
                      value={minEntries ?? ""}
                      onChange={(event) => {
                        const raw = event.target.value;
                        patch(index, {
                          min_entries: raw === "" ? null : { count: Number(raw) },
                        });
                      }}
                    />
                    <span className="muted small">条</span>
                  </label>
                  <select
                    className="text-input"
                    aria-label={`${section.section_id} 只数哪种`}
                    value={
                      typeof section.min_entries === "object" &&
                      section.min_entries !== null &&
                      typeof section.min_entries.kind === "string"
                        ? section.min_entries.kind
                        : ""
                    }
                    onChange={(event) => {
                      const kind = event.target.value;
                      patch(index, {
                        min_entries: {
                          count: minEntries ?? 1,
                          ...(kind === "" ? {} : { kind }),
                        },
                      });
                    }}
                  >
                    <option value="">不限定</option>
                    {kinds.map((kind) => (
                      <option key={kind} value={kind}>
                        {kind}
                      </option>
                    ))}
                  </select>
                </>
              )}
              <button
                className="iconbtn"
                type="button"
                aria-label="上移这一章"
                disabled={index === 0}
                onClick={() => onChange({ sections: moveSection(sections, index, -1) })}
              >
                ↑
              </button>
              <button
                className="iconbtn"
                type="button"
                aria-label="下移这一章"
                disabled={index === sections.length - 1}
                onClick={() => onChange({ sections: moveSection(sections, index, 1) })}
              >
                ↓
              </button>
              <button
                className="iconbtn"
                type="button"
                aria-label="删掉这一章"
                title={anchor ? "锚点章不能删" : undefined}
                disabled={anchor}
                onClick={() =>
                  onChange({ sections: sections.filter((_, at) => at !== index) })
                }
              >
                ✕
              </button>
            </div>

            <label className="row">
              <span className="muted small">这一章是干什么的</span>
              <input
                className="text-input"
                aria-label={`${section.section_id} 这一章是干什么的`}
                value={section.purpose}
                onChange={(event) => patch(index, { purpose: event.target.value })}
              />
            </label>

            <div className="stack" style={{ gap: "var(--space-1)" }}>
              <div className="row">
                <span className="muted small">槽位</span>
                {section.slots.length === 0 && <span className="muted small">（无）</span>}
              </div>
              {section.slots.map((slot, slotIndex) => (
                <div key={slot.name} className="row">
                  <input
                    className="text-input mono"
                    aria-label={`槽位名 ${slotIndex + 1}`}
                    value={slot.name}
                    onChange={(event) =>
                      patch(index, {
                        slots: section.slots.map((item, at) =>
                          at === slotIndex ? { ...item, name: event.target.value } : item,
                        ),
                      })
                    }
                  />
                  <input
                    className="text-input"
                    aria-label={`槽位标题 ${slotIndex + 1}`}
                    value={slot.title}
                    onChange={(event) =>
                      patch(index, {
                        slots: section.slots.map((item, at) =>
                          at === slotIndex ? { ...item, title: event.target.value } : item,
                        ),
                      })
                    }
                  />
                  <select
                    className="text-input"
                    aria-label={`槽位种类 ${slotIndex + 1}`}
                    value={slot.kind}
                    onChange={(event) =>
                      patch(index, {
                        slots: section.slots.map((item, at) =>
                          at === slotIndex ? { ...item, kind: event.target.value } : item,
                        ),
                      })
                    }
                  >
                    {kinds.map((kind) => (
                      <option key={kind} value={kind}>
                        {kind}
                      </option>
                    ))}
                  </select>
                  <button
                    className="iconbtn"
                    type="button"
                    aria-label={`删掉槽位 ${slotIndex + 1}`}
                    onClick={() =>
                      patch(index, {
                        slots: section.slots.filter((_, at) => at !== slotIndex),
                      })
                    }
                  >
                    ✕
                  </button>
                </div>
              ))}
              <button
                className="btn btn--ghost"
                type="button"
                aria-label={`给 ${section.section_id} 加槽位`}
                onClick={() =>
                  patch(index, {
                    slots: [
                      ...section.slots,
                      { name: `slot${section.slots.length + 1}`, title: "", kind: kinds[0] ?? "text" },
                    ],
                  })
                }
              >
                + 槽位
              </button>
            </div>
          </section>
        );
      })}

      <button
        className="btn btn--ghost"
        type="button"
        onClick={() => onChange({ sections: addSection(sections) })}
      >
        加一章
      </button>
    </div>
  );
}