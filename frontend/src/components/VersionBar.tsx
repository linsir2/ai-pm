import type { DocumentView, ProjectSummaryView, ReadinessItemView } from "../api/types";
import { readinessHeadline, readinessPercent, readinessSummary } from "../document/readiness";

function HistoryIcon() {
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      <circle cx="8" cy="8" r="6" />
      <path d="M8 4.8V8l2.2 1.4" />
    </svg>
  );
}

export function VersionBar({
  project,
  document: doc,
  readiness,
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
  /** 点"就绪度 N%"看清单（`P29` F6：这一句以前是死文本，点不动）。 */
  onOpenReadiness?: () => void;
  /** 点"记忆 N"看/删（M6）：记忆是用户逐条确认过的东西，要看得见也删得掉。 */
  onOpenMemory?: () => void;
}) {
  if (project === null) return null;
  const version = doc === null ? project.version_no : doc.version_no;
  const headline = readinessHeadline(readiness ?? []);
  const { blockers, warns } = readinessSummary(readiness ?? []);
  const percent = readiness === undefined ? null : readinessPercent(readiness, doc?.sections ?? []);

  return (
    <span className="vbar">
      <span className="vbar__version mono" title="每次写入产生一版，版本只增不改">
        v{version}
      </span>
      {onOpenVersions !== undefined && (
        <button type="button" className="vbar__btn" onClick={onOpenVersions}>
          <HistoryIcon />
          版本历史
        </button>
      )}
      {percent !== null && onOpenReadiness === undefined && (
        <span
          className={blockers > 0 ? "vbar__chip vbar__chip--warn" : "vbar__chip"}
          title={`就绪度：${headline}（阻塞 ${blockers} 项 / 提示 ${warns} 项）`}
        >
          就绪度 {percent}%
        </span>
      )}
      {percent !== null && onOpenReadiness !== undefined && (
        <button
          type="button"
          className={blockers > 0 ? "vbar__chip vbar__chip--warn" : "vbar__chip"}
          onClick={onOpenReadiness}
          title={`就绪度：${headline}（阻塞 ${blockers} 项 / 提示 ${warns} 项）· 点开看按章分组的清单`}
        >
          就绪度 {percent}%
        </button>
      )}
      {onOpenMemory !== undefined && memoryCount > 0 && (
        <button
          type="button"
          className="vbar__link"
          onClick={onOpenMemory}
          title="这个项目确认过的记忆（教训 / 已定口径 / 已否掉）：会影响以后每一轮"
        >
          记忆 {memoryCount}
        </button>
      )}
    </span>
  );
}
