/* 左栏 = 文档结构的树：项目 / 13 章（可展开）/ 章里的功能子树（可展开到字段）/ 偏好区。

   两条规矩：
   1. **树里显示的每一层都来自后端投影**（章节来自 sections，功能与字段来自 s5 的 block 树），
      界面不自己编层级；
   2. 点一层 = 切到那一章并滚到那个节点；点小三角只负责展开收起。
*/

import { useEffect, useState } from "react";

import type { BlockView, DocumentView, ReadinessItemView, RoundView } from "../api/types";
import { STATUS_TEXT, sectionBadge, sectionStatus, statusClass } from "../document/status";
import { readinessPercent, readinessSummary } from "../document/readiness";
import { featureHome, listFeatures } from "../document/structure";
import { fieldTitle } from "../state/schema";
import { ReadinessRing } from "./ReadinessRing";

const PREFERENCE = "preference";

/** 文档里的条目总数（递归数到功能子块）：文档里的条目都是用户确认过的。 */
function countEntries(blocks: readonly BlockView[]): number {
  return blocks.reduce(
    (sum, block) => sum + block.entries.length + countEntries(block.children),
    0,
  );
}

export function SideRail({
  document: doc,
  round,
  readiness = [],
  activeSection,
  onSelectSection,
  onSelectNode,
}: {
  document: DocumentView | null;
  round: RoundView | null;
  /** 后端就绪度判据（阻塞 / 提示）：环形图与摘要从这儿算。 */
  readiness?: ReadinessItemView[];
  activeSection: string;
  onSelectSection: (sectionId: string) => void;
  onSelectNode: (sectionId: string, blockId: string) => void;
}) {
  // 默认展开功能那一章（模板的锚点，不是 s5）。文档还没来时先开着，等文档到了再校正。
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const features = listFeatures(doc);
  const home = featureHome(doc);

  useEffect(() => {
    if (home !== null) setOpen((current) => (current[home] === true ? current : { ...current, [home]: true }));
  }, [home]);

  // 切到哪一章就把它展开：不然点树会像没反应。
  useEffect(() => {
    if (activeSection !== PREFERENCE) {
      setOpen((current) => (current[activeSection] === true ? current : { ...current, [activeSection]: true }));
    }
  }, [activeSection]);

  const toggle = (id: string) =>
    setOpen((current) => ({ ...current, [id]: current[id] !== true }));

  // 就绪度环形图：按章算——有阻塞的章算"未就绪"，其余算"已就绪"（算法与文档区共享）。
  const { blockers, warns } = readinessSummary(readiness);
  const readinessRatio = readinessPercent(readiness, doc?.sections ?? []);
  const entryCount = doc === null ? 0 : countEntries(doc.sections.flatMap((s) => s.blocks));
  const hasDoc = doc !== null && entryCount > 0;

  return (
    <nav className="rail" aria-label="文档结构">
      <div className="rail__doc-header">
        <span className="rail__doc-title">产品文档</span>
      </div>

      {hasDoc && (
        <div className="rail__readiness">
          <ReadinessRing percent={readinessRatio} />
          <div className="rail__readiness-text">
            <span className="rail__readiness-label">
              整体进度 {blockers === 0 ? "良好" : "待补"}
            </span>
            <span className="rail__readiness-count">
              已确认 {entryCount} 条
              {blockers > 0 && ` · 阻塞 ${blockers} 项`}
              {blockers === 0 && warns > 0 && ` · 提示 ${warns} 项`}
            </span>
          </div>
        </div>
      )}

      <p className="rail__head">
        章节导航 <span className="rail__head-count">{doc?.sections.length ?? 0}</span>
      </p>
      <ul className="rail__list rail__tree" role="tree">
        {doc?.sections.map((section) => {
          const status = sectionStatus(section, round);
          const badge = sectionBadge(section.section_id, readiness);
          const on = activeSection === section.section_id;
          const children =
            home !== null && section.section_id === home
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
                  <span className={statusClass(status)} role="img" aria-label={STATUS_TEXT[status]} />
                  {badge !== null && (
                    <span
                      className="rail__badge mono"
                      style={badge.block > 0 ? { color: "var(--conflict)" } : { color: "var(--gap)" }}
                    >
                      {badge.block > 0 ? `!${badge.block}` : `${badge.warn}`}
                    </span>
                  )}
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
                          onClick={() => home !== null && onSelectNode(home, child.id)}
                          title={`${home} · feature#${child.slug}`}
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
                                onClick={() => home !== null && onSelectNode(home, kid.id)}
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
      <p className="rail__legend">矛盾 &gt; 开放问题 &gt; 缺口 &gt; 有内容 &gt; 空</p>
    </nav>
  );
}
