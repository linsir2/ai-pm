/* 文档区：一次读一章（换章走左栏那棵树，所以这里不再放一排章节按钮）。
   章节头那行"这一章是干什么的"来自 `/schema`（后端 `SectionSpec.purpose` 的唯一一份），
   界面不编一句；拿不到 schema 就不显示。 */

import type { DocumentView, ReadinessItemView, RoundView, SchemaView } from "../api/types";
import { readinessItemsFor } from "../document/readiness";
import { STATUS_TEXT, sectionStatus, statusClass } from "../document/status";
import { chapterAnchors } from "../document/structure";
import { sectionPurpose } from "../state/schema";
import { BlockTree } from "./BlockTree";
import { DiscussHandle } from "./DiscussHandle";
import { EntryList } from "./EntryList";
import { FeatureBlock } from "./FeatureBlock";
import { Skeleton } from "./States";

export const PREFERENCE_AREA = "preference-area";

export function DocumentPane({
  document: doc,
  round,
  justWritten,
  activeSection,
  readiness = [],
  schema = null,
}: {
  document: DocumentView | null;
  round: RoundView | null;
  justWritten: string[];
  activeSection: string;
  readiness?: ReadinessItemView[];
  /** `GET /schema`：章节说明从这儿取。 */
  schema?: SchemaView | null;
}) {
  if (doc === null) {
    return (
      <main className="doc" aria-label="产品文档">
        <div className="doc__inner">
          <Skeleton lines={6} />
        </div>
      </main>
    );
  }

  if (activeSection === "preference") {
    return (
      <main className="doc" aria-label="产品文档">
        <div className="doc__inner">
          <section className="prefs" id="sec-preference">
            <h2 className="prefs__head">偏好区</h2>
            <p className="prefs__note">
              你的口味记在这里：不进正文、不进交付物，只影响后面怎么写。
            </p>
            <EntryList
              blockId={PREFERENCE_AREA}
              entries={doc.preference_entries}
              kind="bullet_list"
            />
          </section>
          <DiscussHandle sectionKey="preference" />
        </div>
      </main>
    );
  }

  const section =
    doc.sections.find((item) => item.section_id === activeSection) ?? doc.sections[0]!;
  const status = sectionStatus(section, round);
  const anchors = chapterAnchors(doc, section.section_id);
  const gaps = readinessItemsFor(readiness, section.section_id);
  const purpose = sectionPurpose(section.section_id, schema);

  return (
    <main className="doc" aria-label="产品文档">
      <div className="doc__inner">
        <section className="chapter">
          <header className="chapter__head">
            <div className="chapter__kicker">
              <span className="mono">{section.section_id}</span>
              <span className="muted small">{doc.title}</span>
              <span className="topbar__spacer" />
              <span className={statusClass(status)} />
              <span className="muted small">{STATUS_TEXT[status]}</span>
            </div>
            <h2 className="chapter__title">{section.title}</h2>
            {purpose !== null && <p className="chapter__purpose muted small">{purpose}</p>}
          </header>

          {anchors.length > 0 && (
            <nav className="anchors" aria-label="本页结构">
              {anchors.map((anchor) => (
                <a className="anchors__item" href={`#${anchor.id}`} key={anchor.id}>
                  {anchor.text}
                </a>
              ))}
            </nav>
          )}

          {gaps.length > 0 && (
            <ul className="chapter__gaps" aria-label="这一章按合格标准还差什么">
              {gaps.map((item) => (
                <li className={item.severity === "blocker" ? "gap gap--blocker" : "gap"} key={item.code}>
                  {item.severity === "blocker" ? "阻塞" : "提示"}：{item.title}
                  {item.detail !== "" && <span className="muted"> · {item.detail}</span>}
                </li>
              ))}
            </ul>
          )}

          <DiscussHandle sectionKey={section.section_id} />
          <div className="chapter__body">
            {section.blocks.length === 0 ? (
              <div className="chapter__empty">
                <p className="section__blank">这一章还没写。你也可以自己先写一条。</p>
                <EntryList
                  blockId={section.section_id}
                  entries={[]}
                  kind="text"
                  addLabel="+ 在这一章写第一条"
                />
                {pendingNote(round, section.section_id) !== null && (
                  <p className="section__note">{pendingNote(round, section.section_id)}</p>
                )}
              </div>
            ) : (
              section.blocks.map((block) =>
                block.kind === "feature" ? (
                  <FeatureBlock feature={block} justWritten={justWritten} key={block.block_id} />
                ) : (
                  <BlockTree block={block} justWritten={justWritten} key={block.block_id} />
                ),
              )
            )}
          </div>
        </section>
      </div>
    </main>
  );
}

/** 空章节上说一句"本轮有东西落在这儿"，比干说"还没写"有用。 */
function pendingNote(round: RoundView | null, sectionId: string): string | null {
  if (round === null) return null;
  const inSection = (target: string) => target.split(/[/.]/)[0] === sectionId;
  const questions = round.questions.filter((question) => inSection(question.target_label)).length;
  const proposals = (round.draft?.proposals ?? []).filter((proposal) =>
    inSection(proposal.target_label),
  ).length;
  const parts: string[] = [];
  if (questions > 0) parts.push(`${questions} 个问题在问你`);
  if (proposals > 0) parts.push(`${proposals} 条候选等你勾`);
  return parts.length === 0 ? null : `本轮有 ${parts.join("、")}。`;
}
