import type { AuthUser } from "../api/client";
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

export function TopBar({
  projects,
  project,
  connection,
  onSelectProject,
  onExport,
  /** 左上角标识：点了回项目列表。没有项目时不传（顶栏就没有可回的
      地方——那一屏本来就在 `Onboarding`）。 */
  onHome,
  onTemplates,
  templatesActive = false,
  libraryCount = 0,
  onOpenLibrary,
  theme,
  onToggleTheme,
  docOpen = true,
  onToggleDoc,
  search = null,
  user,
  onLogout,
}: {
  projects: ProjectSummaryView[];
  project: ProjectSummaryView | null;
  connection: "idle" | "open" | "lost";
  onSelectProject: (projectId: string) => void;
  onHome?: () => void;
  /** 模板库入口（`06` §2.3）：不传就不画（工作区里没有它，模板管理是列表页的事）。 */
  onTemplates?: () => void;
  /** 模板库视图里标出当前位置（`06` §2.4 §1）：跨视图按钮按 ARIA 取 `"page"`。
      与 `SideRail` 章节按钮的布尔值、`ProjectList` 页内锚点的 `"location"` 各表各的语义，
      不互相照抄（旧注"照抄 SideRail"与实情不符，改成如实描述）。 */
  templatesActive?: boolean;
  onExport: () => void;
  /** 材料库入口（顶栏）：显示库里有几份，点开抽屉。 */
  libraryCount?: number;
  onOpenLibrary?: () => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  /** 文档列开着没（折叠开关的状态；不传就不出这个按钮）。 */
  docOpen?: boolean;
  /** 文档列折叠开关：想看才打开（右列 Codex 化之后文档默认让位）。 */
  onToggleDoc?: () => void;
  /** 搜索框（F6）：有项目才有；没项目时传 null，顶栏就不显示它。 */
  search?: {
    query: string;
    results: SearchResultsView | null;
    pending: boolean;
    onQueryChange: (query: string) => void;
    onPick: (hit: SearchHitView) => void;
  } | null;
  /** 当前用户（Task 7）：传了就显示用户菜单；不传整块不画（未登录页用不到顶栏）。 */
  user?: AuthUser;
  /** 登出：用户菜单里的按钮。 */
  onLogout?: () => void;
}) {
  const connectionText =
    connection === "open"
      ? "推送已连上"
      : connection === "lost"
        ? "推送断了，正在重连"
        : "推送连接中";

  return (
    <header className="topbar">
      {onHome !== undefined ? (
        <button type="button" className="wordmark wordmark--button" onClick={onHome}
          title="查看所有项目">
          <Logo />
          <span className="wordmark__name">PM Studio</span>
        </button>
      ) : (
        <span className="wordmark">
          <Logo />
          <span className="wordmark__name">PM Studio</span>
        </span>
      )}

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

      {onTemplates !== undefined && (
        <button
          type="button"
          className={templatesActive ? "topbar__action topbar__action--active" : "topbar__action"}
          aria-current={templatesActive ? "page" : undefined}
          onClick={onTemplates}
        >
          模板
        </button>
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
          <button type="button" className="btn btn--ghost btn--sm" onClick={onExport}>
            导出
          </button>
          {onToggleDoc !== undefined && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onToggleDoc}
              aria-pressed={docOpen}
              title="文档列收起 / 展开（想看才打开）"
            >
              文档
            </button>
          )}
        </>
      )}

      <span className={`conn conn--${connection}`} title={connectionText}>
        <span className="conn__dot" aria-hidden="true" />
        <span
          className={connection === "open" ? "muted small conn__label sr-only" : "muted small conn__label"}
          role="status"
        >
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

      {user !== undefined && (
        <span className="topbar__user">
          <span className="topbar__user-name" title={user.role}>
            {user.username}
          </span>
          {onLogout !== undefined && (
            <button type="button" className="btn btn--ghost btn--sm" onClick={onLogout}>
              登出
            </button>
          )}
        </span>
      )}
    </header>
  );
}
