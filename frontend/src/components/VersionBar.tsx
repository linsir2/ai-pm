import type { DocumentView, ProjectSummaryView, ReadinessItemView } from "../api/types";
import { filledSectionCount } from "../document/status";
import { readinessHeadline } from "../document/readiness";

export function VersionBar({
  project,
  document: doc,
  readiness = [],
  memoryCount = 0,
  onOpenVersions,
  onOpenReadiness,
  onOpenMemory,
}: {
  project: ProjectSummaryView | null;
  document: DocumentView | null;
  readiness?: ReadinessItemView[];
  /** 已确认的跨轮记忆条数（M6）：0 时不显示那个入口。 */
  memoryCount?: number;
  onOpenVersions?: () => void;
  /** 点"就差 N 项"看清单（`P29` F6：这一句以前是死文本，点不动）。 */
  onOpenReadiness?: () => void;
  /** 点"记忆 N"看/删（M6）：记忆是用户逐条确认过的东西，要看得见也删得掉。 */
  onOpenMemory?: () => void;
}) {
  if (project === null) return null;
  const version = doc === null ? project.version_no : doc.version_no;
  const filled = filledSectionCount(doc);
  const headline = readinessHeadline(readiness);

  return (
    <span className="mono" title="每次写入产生一版，版本只增不改">
      v{version} · {filled} / {doc?.sections.length ?? 13} 章有内容
      {" · "}
      {onOpenReadiness === undefined ? (
        <span className={readiness === undefined || headline === "就绪" ? "" : "readiness"}>
          {headline}
        </span>
      ) : (
        <button
          type="button"
          className={
            readiness === undefined || headline === "就绪"
              ? "versionbar__link"
              : "versionbar__link readiness"
          }
          onClick={onOpenReadiness}
          title="看还差哪些（按章分组，阻塞 / 提示分开）"
        >
          {headline}
        </button>
      )}
      {onOpenVersions !== undefined && (
        <>
          {" · "}
          <button type="button" className="versionbar__link" onClick={onOpenVersions}>
            版本历史
          </button>
        </>
      )}
      {onOpenMemory !== undefined && memoryCount > 0 && (
        <>
          {" · "}
          <button
            type="button"
            className="versionbar__link"
            onClick={onOpenMemory}
            title="这个项目确认过的记忆（教训 / 已定口径 / 已否掉）：会影响以后每一轮"
          >
            记忆 {memoryCount}
          </button>
        </>
      )}
    </span>
  );
}
