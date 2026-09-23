import type { DocumentView, RoundView } from "../api/types";
import { STATUS_TEXT, sectionStatus } from "../document/status";

export function ChapterTabs({
  document: doc,
  round,
  activeSection,
  onSelect,
}: {
  document: DocumentView;
  round: RoundView | null;
  activeSection: string;
  onSelect: (sectionId: string) => void;
}) {
  return (
    <div className="tabs" role="tablist" aria-label="章节">
      {doc.sections.map((section) => {
        const status = sectionStatus(section, round);
        const on = activeSection === section.section_id;
        return (
          <button
            key={section.section_id}
            type="button"
            role="tab"
            id={`tab-${section.section_id}`}
            aria-controls={`chapter-${section.section_id}`}
            aria-selected={on}
            className={on ? "tab tab--on" : "tab"}
            onClick={() => onSelect(section.section_id)}
            title={`${section.title} · ${STATUS_TEXT[status]}`}
          >
            <span className="tab__num mono">{section.section_id.slice(1)}</span>
            {section.title}
            <span className={`dot dot--${status}`} />
          </button>
        );
      })}
      {doc.preference_entries.length > 0 && (
        <button
          type="button"
          role="tab"
          id="tab-preference"
          aria-controls="chapter-preference"
          aria-selected={activeSection === "preference"}
          className={activeSection === "preference" ? "tab tab--on" : "tab"}
          onClick={() => onSelect("preference")}
        >
          <span className="tab__num mono">—</span>
          偏好区
        </button>
      )}
    </div>
  );
}
