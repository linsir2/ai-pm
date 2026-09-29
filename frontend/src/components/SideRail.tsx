/* 左栏 = 文档结构的树：项目 / 13 章（可展开）/ 章里的功能子树（可展开到字段）/ 偏好区。

   两条规矩：
   1. **树里显示的每一层都来自后端投影**（章节来自 sections，功能与字段来自 s5 的 block 树），
      界面不自己编层级；
   2. 点一层 = 切到那一章并滚到那个节点；点小三角只负责展开收起。
*/

import { useEffect, useState } from "react";

import type { DocumentView, ProjectSummaryView, RoundView } from "../api/types";
import { STATUS_TEXT, sectionStatus, statusClass } from "../document/status";
import { listFeatures } from "../document/structure";
import { fieldTitle } from "../state/schema";

const PREFERENCE = "preference";

export function SideRail({
  projects,
  project,
  document: doc,
  round,
  activeSection,
  onSelectProject,
  onSelectSection,
  onSelectNode,
}: {
  projects: ProjectSummaryView[];
  project: ProjectSummaryView | null;
  document: DocumentView | null;
  round: RoundView | null;
  activeSection: string;
  onSelectProject: (projectId: string) => void;
  onSelectSection: (sectionId: string) => void;
  onSelectNode: (sectionId: string, blockId: string) => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({ s5: true });
  const features = listFeatures(doc);

  // 切到哪一章就把它展开：不然点树会像没反应。
  useEffect(() => {
    if (activeSection !== PREFERENCE) {
      setOpen((current) => (current[activeSection] === true ? current : { ...current, [activeSection]: true }));
    }
  }, [activeSection]);

  const toggle = (id: string) =>
    setOpen((current) => ({ ...current, [id]: current[id] !== true }));

  return (
    <nav className="rail" aria-label="文档结构">
      <p className="rail__head">我的项目</p>
      <ul className="rail__list">
        {projects.map((item) => {
          const on = project?.project_id === item.project_id;
          return (
            <li key={item.project_id}>
              <button
                type="button"
                className={on ? "rail__item rail__item--on" : "rail__item"}
                onClick={() => onSelectProject(item.project_id)}
                aria-current={on}
              >
                <span className="rail__label">{item.title}</span>
                <span className="rail__meta mono">v{item.version_no}</span>
              </button>
            </li>
          );
        })}
      </ul>

      <p className="rail__head rail__head--gap">文档结构</p>
      <ul className="rail__list rail__tree" role="tree">
        {doc?.sections.map((section) => {
          const status = sectionStatus(section, round);
          const on = activeSection === section.section_id;
          const children =
            section.section_id === "s5"
              ? features.flatMap((feature) => [
                  {
                    id: `block-${feature.blockId}`,
                    text: feature.name,
                    slug: feature.slug,
                    kids: feature.block.children
                      .filter((child) => child.field !== null && child.field !== "name")
                      .map((child) => ({
                        id: `block-${child.block_id}`,
                        text: fieldTitle(child.field) ?? "",
                      })),
                  },
                ])
              : [];
          const expanded = open[section.section_id] === true;
          return (
            <li key={section.section_id}>
              <div className={on ? "rail__row rail__row--on" : "rail__row"}>
                <button
                  type="button"
                  className={children.length > 0 ? "rail__twisty" : "rail__twisty rail__twisty--empty"}
                  onClick={() => children.length > 0 && toggle(section.section_id)}
                  aria-expanded={children.length > 0 ? expanded : undefined}
                  aria-label={expanded ? "收起" : "展开"}
                >
                  {children.length > 0 ? (expanded ? "▾" : "▸") : "·"}
                </button>
                <button
                  type="button"
                  className="rail__item rail__item--flush"
                  onClick={() => onSelectSection(section.section_id)}
                  title={`${section.title} · ${STATUS_TEXT[status]}`}
                  aria-current={on}
                >
                  <span className="rail__num mono">{section.section_id}</span>
                  <span className="rail__label">{section.title}</span>
                  <span className={statusClass(status)} />
                </button>
              </div>

              {expanded && children.length > 0 && (
                <ul className="rail__list rail__kids" role="group">
                  {children.map((child) => (
                    <li key={child.id}>
                      <div className="rail__row">
                        <span className="rail__twisty rail__twisty--empty">·</span>
                        <button
                          type="button"
                          className="rail__item rail__item--flush rail__item--feature"
                          onClick={() => onSelectNode("s5", child.id)}
                          title={`s5 · feature#${child.slug}`}
                        >
                          <span className="feature__slug">feature#{child.slug}</span>
                          <span className="rail__label">{child.text}</span>
                        </button>
                      </div>
                      {child.kids.length > 0 && (
                        <ul className="rail__list rail__grandkids">
                          {child.kids.map((kid) => (
                            <li key={kid.id}>
                              <button
                                type="button"
                                className="rail__item rail__item--leaf"
                                onClick={() => onSelectNode("s5", kid.id)}
                              >
                                <span className="rail__label">{kid.text}</span>
                              </button>
                            </li>
                          ))}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}

        {doc !== null && doc.preference_entries.length > 0 && (
          <li>
            <div className="rail__row">
              <span className="rail__twisty rail__twisty--empty">·</span>
              <button
                type="button"
                className="rail__item rail__item--flush"
                onClick={() => onSelectSection(PREFERENCE)}
                aria-current={activeSection === PREFERENCE}
                title="偏好区 · 不进正文、不进交付"
              >
                <span className="rail__num mono">—</span>
                <span className="rail__label">偏好区</span>
                <span className="dot dot--open" />
              </button>
            </div>
          </li>
        )}
      </ul>
    </nav>
  );
}
