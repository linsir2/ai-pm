/* 左栏：项目、文档结构（13 章）、功能子树。
   三块都是从后端投影里推出来的，没有任何一块需要新接口。 */

import type { DocumentView, ProjectSummaryView, RoundView } from "../api/types";
import { STATUS_TEXT, sectionStatus, statusClass } from "../document/status";
import { listFeatures } from "../document/structure";

export function SideRail({
  projects,
  project,
  document: doc,
  round,
  activeSection,
  onSelectProject,
  onSelectSection,
  onSelectFeature,
}: {
  projects: ProjectSummaryView[];
  project: ProjectSummaryView | null;
  document: DocumentView | null;
  round: RoundView | null;
  activeSection: string;
  onSelectProject: (projectId: string) => void;
  onSelectSection: (sectionId: string) => void;
  onSelectFeature: (blockId: string) => void;
}) {
  const features = listFeatures(doc);

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
      <ul className="rail__list">
        {doc?.sections.map((section) => {
          const status = sectionStatus(section, round);
          const on = activeSection === section.section_id;
          return (
            <li key={section.section_id}>
              <button
                type="button"
                className={on ? "rail__item rail__item--on" : "rail__item"}
                onClick={() => onSelectSection(section.section_id)}
                title={`${section.title} · ${STATUS_TEXT[status]}`}
                aria-current={on}
              >
                <span className="rail__num mono">{section.section_id}</span>
                <span className="rail__label">{section.title}</span>
                <span className={statusClass(status)} role="img" aria-label={STATUS_TEXT[status]} />
              </button>
            </li>
          );
        })}
        {doc !== null && doc.preference_entries.length > 0 && (
          <li>
            <button
              type="button"
              className={activeSection === "preference" ? "rail__item rail__item--on" : "rail__item"}
              onClick={() => onSelectSection("preference")}
              title="偏好区 · 不进正文、不进交付"
            >
              <span className="rail__num mono">—</span>
              <span className="rail__label">偏好区</span>
              <span className="dot dot--open" role="img" aria-label="有偏好记录" />
            </button>
          </li>
        )}
      </ul>

      {features.length > 0 && (
        <>
          <p className="rail__head rail__head--gap">功能子树</p>
          <ul className="rail__list">
            {features.map((feature) => (
              <li key={feature.blockId}>
                <button
                  type="button"
                  className="rail__item rail__item--feature"
                  onClick={() => onSelectFeature(feature.blockId)}
                  title={`s5 · feature#${feature.slug}`}
                >
                  <span className="feature__slug">feature#{feature.slug}</span>
                  <span className="rail__label">{feature.name}</span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </nav>
  );
}
