import type { ProjectSummaryView, SearchHitView, SearchResultsView } from "../api/types";
import { Logo } from "./Logo";
import { SearchBox } from "./SearchBox";

function FolderIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      <path d="M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5h4.5A1.5 1.5 0 0 1 14 6v6a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12V4.5Z" />
    </svg>
  );
}

function BulbIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      aria-hidden="true"
    >
      <path d="M8 1.5a4.5 4.5 0 0 1 2.5 8.24V11h-5V9.74A4.5 4.5 0 0 1 8 1.5Z" />
      <path d="M6.5 13.5h3" />
    </svg>
  );
}

export function TopBar({
  projects,
  project,
  connection,
  onSelectProject,
  onExport,
  libraryCount = 0,
  onOpenLibrary,
  inboxCount = 0,
  onOpenInbox,
  theme,
  onToggleTheme,
  search = null,
}: {
  projects: ProjectSummaryView[];
  project: ProjectSummaryView | null;
  connection: "idle" | "open" | "lost";
  onSelectProject: (projectId: string) => void;
  onExport: () => void;
  /** 材料库入口（顶栏）：显示库里有几份，点开抽屉。 */
  libraryCount?: number;
  onOpenLibrary?: () => void;
  /** 灵感箱入口（顶栏）：未处理数徽标，点开切到灵感箱页签。 */
  inboxCount?: number;
  onOpenInbox?: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  /** 搜索框（F6）：有项目才有；没项目时传 null，顶栏就不显示它。 */
  search?: {
    query: string;
    results: SearchResultsView | null;
    pending: boolean;
    onQueryChange: (query: string) => void;
    onPick: (hit: SearchHitView) => void;
  } | null;
}) {
  const connectionText =
    connection === "open"
      ? "推送已连上"
      : connection === "lost"
        ? "推送断了，正在重连"
        : "推送连接中";

  return (
    <header className="topbar">
      <span className="wordmark">
        <Logo />
        <span className="wordmark__name">PM Studio</span>
      </span>

      {project !== null && (
        <>
          <label className="sr-only" htmlFor="project-switch">
            切换项目
          </label>
          <span className="project-switch__wrap">
            <select
              id="project-switch"
              className="project-switch"
              value={project.project_id}
              onChange={(event) => onSelectProject(event.target.value)}
            >
              {projects.map((item) => (
                <option key={item.project_id} value={item.project_id}>
                  {item.title}
                </option>
              ))}
            </select>
            <span className="project-switch__caret" aria-hidden="true">
              ▾
            </span>
          </span>
        </>
      )}

      <span className="topbar__spacer" />
      {search !== null && <SearchBox {...search} />}
      <span className="topbar__spacer" />

      {project !== null && (
        <>
          {onOpenLibrary !== undefined && (
            <button
              type="button"
              className="topbar__action"
              onClick={onOpenLibrary}
              title="项目里的全部材料"
            >
              <FolderIcon />
              材料库
              {libraryCount > 0 && <span className="topbar__action-count">{libraryCount}</span>}
            </button>
          )}
          {onOpenInbox !== undefined && (
            <button
              type="button"
              className="topbar__action"
              onClick={onOpenInbox}
              title="讨论卡与调研候选统一过目"
            >
              <BulbIcon />
              灵感箱
              {inboxCount > 0 && <span className="topbar__badge">{inboxCount}</span>}
            </button>
          )}
          <button type="button" className="btn btn--sm" onClick={onExport}>
            导出
          </button>
        </>
      )}

      <span className={`conn conn--${connection}`}>
        <span className="conn__dot" aria-hidden="true" />
        <span className="muted small conn__label" role="status">
          {connectionText}
        </span>
      </span>
      <button
        type="button"
        className="btn btn--ghost btn--sm"
        onClick={onToggleTheme}
        aria-pressed={theme === "dark"}
        title="切换明暗（只影响界面，不影响文档内容）"
      >
        {theme === "dark" ? "亮色" : "暗色"}
      </button>
    </header>
  );
}
