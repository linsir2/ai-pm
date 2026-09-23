import type { DocumentView, ProjectSummaryView } from "../api/types";
import { stepText } from "../labels";
import { Logo } from "./Logo";
import { VersionBar } from "./VersionBar";

export function TopBar({
  projects,
  project,
  document: doc,
  roundStep,
  connection,
  onSelectProject,
  theme,
  onToggleTheme,
}: {
  projects: ProjectSummaryView[];
  project: ProjectSummaryView | null;
  document: DocumentView | null;
  roundStep: string | null;
  connection: "idle" | "open" | "lost";
  onSelectProject: (projectId: string) => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
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
        <span className="wordmark__sub">产品文档工作台</span>
      </span>
      <span className="topbar__divider" />

      {project !== null && (
        <>
          <label className="sr-only" htmlFor="project-switch">
            切换项目
          </label>
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
          <VersionBar project={project} document={doc} />
          {roundStep !== null && <span className="pill-step">{stepText(roundStep)}</span>}
        </>
      )}

      <span className="topbar__spacer" />

      <span className={`conn conn--${connection}`}>
        <span className="conn__dot" />
        <span className="sr-only">{connectionText}</span>
        <span className="muted small">{connectionText}</span>
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
