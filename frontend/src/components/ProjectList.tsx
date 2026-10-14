/* 项目列表：门户主界面。左导航壳 + 右内容（上画廊下密行），Word/WPS 起始屏同构：
   上面解决"开个新的"（模板点选即建），下面解决"回去接着写"（密行，一行看全）。
   左栏现在三项，全是页内锚点——多用户那天导航变视图，壳不动（`02` §11.0.68 planning）。

**为什么是"顶层视图"而不是一条路由**：本仓前端零路由依赖（三栏工作区是"一个页面"的
心智），而 `useWorkspaceData` 早就在用 `window.history.replaceState` + `?project=` 记项目。
再引一个路由库要改 App 顶层结构、要处理刷新恢复——为这一屏付那个代价不值。

**一个项目都没有时不画空列表**：直接复用 `Onboarding`。空态只有一份，
两份空态就会漂移（"先建一个项目"那句话改一处忘一处）。

**这一屏一个业务判断都不做**（`App.tsx` 的既有纪律）。行上只摆摘要字段，
不为每一行调一次 `/workspace`（N+1）；模板名来自摘要里的 `template_title`
（后端行快照里带出来，不编）。 */

import type { LimitsView, ProjectSummaryView, TemplateView } from "../api/types";
import { Logo } from "./Logo";
import { NewProjectForm } from "./NewProjectForm";
import { Onboarding } from "./Onboarding";
import { Skeleton } from "./States";

export function ProjectList({
  projects,
  onOpen,
  onCreate,
  pending,
  error,
  booting,
  limits,
  model = null,
  templates,
}: {
  projects: ProjectSummaryView[];
  onOpen: (projectId: string) => void;
  onCreate: (title: string, templateId: string) => void;
  pending: boolean;
  /** 建项目失败时的那一句：只有自己这一屏的 `NewProjectForm` 会渲染它。 */
  error: string | null;
  booting: boolean;
  /** 现行上限（`/schema.limits`）：只用来在页眉小字里显示"席位轮上限"。 */
  limits: LimitsView;
  /** 模型名（`/health`）。查不到就是 null，那一格显示 —，不猜。 */
  model?: string | null;
  /** 模板库（`/schema.templates`）：画廊的数据源；空表退化成纯表单。 */
  templates: TemplateView[];
}) {
  if (booting) {
    return (
      <div className="doc__inner home">
        <Skeleton lines={5} label="正在读项目列表" />
      </div>
    );
  }

  if (projects.length === 0) {
    // 空态复用同一个组件：表单、说明文、错误横幅全都是一份
    return (
      <div className="doc__inner home">
        <Onboarding
          onCreate={onCreate}
          error={error}
          pending={pending}
          templates={templates}
        />
      </div>
    );
  }

  return (
    <div className="doc__inner home home--split">
      <nav className="home__nav" aria-label="项目页导航">
        <a className="home__navlink" href="#home-new">
          新建项目
        </a>
        <a className="home__navlink" href="#home-recent">
          最近项目
        </a>
        <a className="home__navlink" href="#home-templates">
          模板库
        </a>
      </nav>
      <div className="home__main">
        <header className="home__head">
          {/* 页面标识：40px primary 方块里放同一个 Logo。不用状态色——
              缺口/矛盾/开放是语义色，四处必须同一个颜色，挪用去做装饰就破了那条纪律。 */}
          <span className="home__mark" aria-hidden="true">
            <Logo />
          </span>
          <h1 className="home__title">你的项目</h1>
          <p className="home__lede">
            点一行回去接着写；也可以另开一个——先选模板，再起名。
          </p>
        </header>

        <p className="home__meta">
          {projects.length} 个项目 · 最近更新 {newestOf(projects) ?? "—"} ·{" "}
          {model ?? "—"} · 席位轮上限 {limits.max_seat_rounds} 轮
        </p>

        <section id="home-new" aria-label="新建项目">
          <NewProjectForm
            templates={templates}
            onCreate={onCreate}
            pending={pending}
            error={error}
          />
        </section>

        <section id="home-recent" aria-label="最近项目">
          <ul className="projects" role="list">
            {projects.map((item) => (
              <li key={item.project_id} className="projects__item">
                <button
                  type="button"
                  className="projects__row"
                  onClick={() => onOpen(item.project_id)}
                >
                  <span className="projects__glyph" aria-hidden="true">
                    {glyphOf(item.title)}
                  </span>
                  <span className="projects__title">{item.title}</span>
                  <span className="projects__meta">
                    <span className="projects__version">v{item.version_no}</span>
                    <span className="projects__time">{item.updated_at.slice(0, 10)}</span>
                    <span className="projects__template">{item.template_title}</span>
                    <span className="projects__go" aria-hidden="true">
                      ›
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}

/** 最近更新的那一天：列表已按 `updated_at` 倒序（后端排序），取第一个即可。 */
function newestOf(projects: ProjectSummaryView[]): string | null {
  const first = projects[0];
  return first === undefined ? null : first.updated_at.slice(0, 10);
}

/** 行的身份方块用哪个字：标题第一个非空白字符。空标题回退到一个不显眼的点。 */
function glyphOf(title: string): string {
  const trimmed = title.trim();
  return trimmed === "" ? "·" : (trimmed[0] ?? "·");
}
