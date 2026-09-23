/* 文档区：一次读一章。上面是章节标签条，下面是这一章的正文。
   章节说明（合格标准）现在只在后端与设计文档里，界面不编一份出来。 */

import type { DocumentView, RoundView } from "../api/types";
import { STATUS_TEXT, sectionStatus, statusClass } from "../document/status";
import { chapterAnchors } from "../document/structure";
import { BlockTree } from "./BlockTree";
import { ChapterTabs } from "./ChapterTabs";
import { EvidenceChip } from "./EvidenceChip";
import { FeatureBlock } from "./FeatureBlock";
import { Skeleton } from "./States";

export function DocumentPane({
  document: doc,
  round,
  justWritten,
  activeSection,
  onSelectSection,
}: {
  document: DocumentView | null;
  round: RoundView | null;
  justWritten: string[];
  activeSection: string;
  onSelectSection: (sectionId: string) => void;
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
          <ChapterTabs
            document={doc}
            round={round}
            activeSection={activeSection}
            onSelect={onSelectSection}
          />
          <section
            className="prefs"
            id="sec-preference"
            role="tabpanel"
            aria-labelledby="tab-preference"
          >
            <h2 className="prefs__head">偏好区</h2>
            <p className="prefs__note">
              你的口味记在这里：不进正文、不进交付物，只影响后面怎么写。
            </p>
            <ul className="entries">
              {doc.preference_entries.map((entry) => (
                <li className="entry" key={entry.entry_id}>
                  <span className="entry__text">{entry.text}</span>
                  <span className="entry__refs">
                    <EvidenceChip eids={entry.refs} />
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </main>
    );
  }

  const section =
    doc.sections.find((item) => item.section_id === activeSection) ?? doc.sections[0]!;
  const status = sectionStatus(section, round);
  const anchors = chapterAnchors(doc, section.section_id);

  return (
    <main className="doc" aria-label="产品文档">
      <div className="doc__inner">
        <ChapterTabs
          document={doc}
          round={round}
          activeSection={section.section_id}
          onSelect={onSelectSection}
        />

        <section
          className="chapter"
          role="tabpanel"
          id={`chapter-${section.section_id}`}
          aria-labelledby={`tab-${section.section_id}`}
        >
          <header className="chapter__head">
            <div className="chapter__kicker">
              <span className="mono">{section.section_id}</span>
              <span className="muted small">{doc.title}</span>
              <span className="topbar__spacer" />
              <span className={statusClass(status)} />
              <span className="muted small">{STATUS_TEXT[status]}</span>
            </div>
            <h2 className="chapter__title">{section.title}</h2>
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

          <div className="chapter__body">
            {section.blocks.length === 0 ? (
              <>
                <p className="section__blank">这一章还没写。</p>
                {pendingNote(round, section.section_id) !== null && (
                  <p className="section__note">{pendingNote(round, section.section_id)}</p>
                )}
              </>
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
