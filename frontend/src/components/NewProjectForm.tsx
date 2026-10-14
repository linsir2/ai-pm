/* 新建表单（一份，两处共用）：模板画廊 + 名输入 + 提交。
   空模板表时退化成纯表单（老后端不断粮）；默认选中 standard（后端创建缺省也是它）。 */

import { useState } from "react";

import type { TemplateView } from "../api/types";
import { Banner } from "./States";

export function NewProjectForm({
  templates,
  onCreate,
  pending,
  error,
  autoFocus = false,
}: {
  templates: TemplateView[];
  onCreate: (title: string, templateId: string) => void;
  pending: boolean;
  error: string | null;
  autoFocus?: boolean;
}) {
  const standard =
    templates.find((item) => item.template_id === "standard") ?? templates[0];
  const [picked, setPicked] = useState(standard?.template_id ?? "standard");
  const [title, setTitle] = useState("");
  const clean = title.trim();

  return (
    <div className="stack" style={{ gap: "var(--space-2)" }}>
      {error !== null && <Banner>{error}</Banner>}
      {templates.length > 0 && (
        <div
          className="tpl-grid"
          id="home-templates"
          role="radiogroup"
          aria-label="选个模板再建"
        >
          {templates.map((item) => (
            <button
              key={item.template_id}
              type="button"
              role="radio"
              aria-checked={picked === item.template_id}
              className={
                picked === item.template_id ? "tpl-card tpl-card--picked" : "tpl-card"
              }
              onClick={() => setPicked(item.template_id)}
            >
              <span className="tpl-card__title">{item.title}</span>
              <span className="tpl-card__meta">{item.sections.length} 章</span>
            </button>
          ))}
        </div>
      )}
      <form
        className="field-inline"
        onSubmit={(event) => {
          event.preventDefault();
          if (clean !== "") onCreate(clean, picked);
        }}
      >
        <label className="sr-only" htmlFor="new-project-title">
          项目名（一般就是产品名）
        </label>
        <input
          id="new-project-title"
          className="text-input"
          value={title}
          placeholder="项目名，例如：订单中台"
          onChange={(event) => setTitle(event.target.value)}
          autoFocus={autoFocus}
        />
        <button className="btn btn--primary" type="submit" disabled={pending || clean === ""}>
          {pending ? "正在建…" : "建项目"}
        </button>
      </form>
    </div>
  );
}
