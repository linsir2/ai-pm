import type { DocumentView, ProjectSummaryView } from "../api/types";
import { filledSectionCount } from "../document/status";

export function VersionBar({
  project,
  document: doc,
}: {
  project: ProjectSummaryView | null;
  document: DocumentView | null;
}) {
  if (project === null) return null;
  const version = doc === null ? project.version_no : doc.version_no;
  const filled = filledSectionCount(doc);

  return (
    <span className="mono" title="每次写入产生一版，版本只增不改">
      v{version} · {filled} / {doc?.sections.length ?? 13} 章有内容
    </span>
  );
}
