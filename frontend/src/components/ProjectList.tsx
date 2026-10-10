/* 项目列表：门户主界面。一个项目一行，点它进那个项目的工作区。

**为什么是"顶层视图"而不是一条路由**：本仓前端零路由依赖（三栏工作区是"一个页面"的
心智），而 `useWorkspaceData` 早就在用 `window.history.replaceState` + `?project=` 记项目。
再引一个路由库要改 App 顶层结构、要处理刷新恢复——为这一屏付那个代价不值。

**一个项目都没有时不画空列表**：直接复用 `Onboarding`。空态只有一份，
两份空态就会漂移（"先建一个项目"那句话改一处忘一处）。

**这一屏一个业务判断都不做**（`App.tsx` 的既有纪律）。页面上三样东西的数据源：
项目数与最近更新来自 `GET /projects`（同一个响应，不新增请求）；席位轮上限来自
`/schema.limits`（前端 `limitsOf()` 早有，含离线回落）。模型名要 `/health`——
由 `useWorkspaceData` 在**挂载这一次屏时**查一次，查不到就不显示那一格
（宁可不显示，也不编一个模型名）。就绪度**不显示**：那个端点的 delivery 面已按 F12 砍掉，
为一个列表页给每项多调一次 `/workspace` 是 N+1。 */

import { useState } from "react";

import type { LimitsView, ProjectSummaryView } from "../api/types";
import { Logo } from "./Logo";
import { Onboarding } from "./Onboarding";
import { Banner, Skeleton } from "./States";

/** 元信息条：四格 hairline（Notion project space 的属性条做法，借形态不借色）。 */
export function ProjectSummary({
  count,
  newest,
  model,
  seatRounds,
}: {
  count: number;
  newest: string | null;
  model: string | null;
  seatRounds: number;
}) {
  return (
    <dl className="summary" aria-label="项目概览" role="region">
      <div className="summary__cell">
        <dt className="summary__label">项目</dt>
        <dd className="summary__value">{count} 个</dd>
      </div>
      <div className="summary__cell">
        <dt className="summary__label">最近更新</dt>
        <dd className="summary__value">{newest ?? "—"}</dd>
      </div>
      <div className="summary__cell">
        <dt className="summary__label">模型</dt>
        {/* `title`：窄屏这一格会被省略号截断（`openai/qwen3.8-flash` → `openai/qwen3.8-f…`），
            悬停能看全。模型名是"这一版跑在什么上"的唯一线索，截断了也得能查。 */}
        <dd className="summary__value summary__value--mono" title={model ?? undefined}>
          {model ?? "—"}
        </dd>
      </div>
      <div className="summary__cell">
        <dt className="summary__label">席位轮上限</dt>
        <dd className="summary__value">{seatRounds} 轮</dd>
      </div>
    </dl>
  );
}

export function ProjectList({
  projects,
  onOpen,
  onCreate,
  pending,
  error,
  booting,
  limits,
  model = null,
}: {
  projects: ProjectSummaryView[];
  onOpen: (projectId: string) => void;
  onCreate: (title: string) => void;
  pending: boolean;
  /** 建项目失败时的那一句：只有自己这一屏的 `CreateProject` 会渲染它。 */
  error: string | null;
  booting: boolean;
  /** 现行上限（`/schema.limits`）：只用来在元信息条里显示"席位轮上限"这一格。 */
  limits: LimitsView;
  /** 模型名（`/health`）。查不到就是 null，那一格显示 —，不猜。 */
  model?: string | null;
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
        <Onboarding onCreate={onCreate} error={error} pending={pending} />
      </div>
    );
  }

  const newest = newestOf(projects);

  return (
    <div className="doc__inner home">
      <header className="home__head">
        {/* 页面标识：40px primary 方块里放同一个 Logo。不用状态色——
            缺口/矛盾/开放是语义色，四处必须同一个颜色，挪用去做装饰就破了那条纪律。 */}
        <span className="home__mark" aria-hidden="true">
          <Logo />
        </span>
        <h1 className="home__title">你的项目</h1>
        <p className="home__lede">
          一个项目 = 一份 13 章的产品文档骨架。点一行回去接着写；也可以另开一个。
        </p>
      </header>

      <ProjectSummary
        count={projects.length}
        newest={newest}
        model={model}
        seatRounds={limits.max_seat_rounds}
      />

      <CreateProject onCreate={onCreate} pending={pending} error={error} />

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
                <span className="projects__go" aria-hidden="true">
                  ›
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
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

/** 新建表单：与 `Onboarding` 里那一份**同一个形状**（去空白、空白禁提交），
    但不抽公共组件——两份各 20 行、各自的说明文案不同，抽出来的 prop 会比组件本身长。 */
function CreateProject({
  onCreate,
  pending,
  error,
}: {
  onCreate: (title: string) => void;
  pending: boolean;
  error: string | null;
}) {
  const [title, setTitle] = useState("");
  const clean = title.trim();

  return (
    <div className="stack" style={{ gap: "var(--space-2)" }}>
      {error !== null && <Banner>{error}</Banner>}
      <form
        className="field-inline"
        onSubmit={(event) => {
          event.preventDefault();
          if (clean !== "") onCreate(clean);
        }}
      >
        <label className="sr-only" htmlFor="new-project-title">
          项目名（一般就是产品名）
        </label>
        <input
          id="new-project-title"
          className="text-input"
          value={title}
          placeholder="项目名，例如：订单中台"
          onChange={(event) => setTitle(event.target.value)}
        />
        <button className="btn btn--primary" type="submit" disabled={pending || clean === ""}>
          {pending ? "正在建…" : "建项目"}
        </button>
      </form>
    </div>
  );
}
