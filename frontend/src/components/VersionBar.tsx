import type { DocumentView, ProjectSummaryView, ReadinessItemView } from "../api/types";
import { filledSectionCount } from "../document/status";
import { readinessHeadline } from "../document/readiness";

export function VersionBar({
  project,
  document: doc,
  readiness = [],
  onOpenVersions,
}: {
  project: ProjectSummaryView | null;
  document: DocumentView | null;
  readiness?: ReadinessItemView[];
  onOpenVersions?: () => void;
}) {
  if (project === null) return null;
  const version = doc === null ? project.version_no : doc.version_no;
  const filled = filledSectionCount(doc);
  const headline = readinessHeadline(readiness);

  return (
    <span className="mono" title="每次写入产生一版，版本只增不改">
      v{version} · {filled} / {doc?.sections.length ?? 13} 章有内容
      <span className={readiness === undefined || headline === "就绪" ? "" : "readiness"}>
        {" · "}
        {headline}
      </span>
      {onOpenVersions !== undefined && (
        <>
          {" · "}
          <button type="button" className="versionbar__link" onClick={onOpenVersions}>
            版本历史
          </button>
        </>
      )}
    </span>
  );
}
