/* 文档中心：最近项目是主内容，新建表单是右侧辅助区，窄屏移到列表下面。

**为什么是"顶层视图"而不是一条路由**：本仓前端零路由依赖（三栏工作区是"一个页面"的
心智），而 `useWorkspaceData` 早就在用 `window.history.replaceState` + `?project=` 记项目。
再引一个路由库要改 App 顶层结构、要处理刷新恢复——为这一屏付那个代价不值。

**一个项目都没有时不画空列表**：直接复用 `Onboarding`。空态只有一份，
两份空态就会漂移（"先建一个项目"那句话改一处忘一处）。

**这一屏一个业务判断都不做**（`App.tsx` 的既有纪律）。行上只摆摘要字段，
不为每一行调一次 `/workspace`（N+1）；模板名来自摘要里的 `template_title`
（后端行快照里带出来，不编）。 */

import { useEffect, useState } from "react";

import type { ProjectSummaryView, TemplateView } from "../api/types";
import { HomeNav } from "./HomeNav";
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
  templates,
  initialTemplateId = null,
  onManageTemplates,
}: {
  projects: ProjectSummaryView[];
  onOpen: (projectId: string) => void;
  onCreate: (title: string, templateId: string) => void;
  pending: boolean;
  /** 建项目失败时的那一句：只有自己这一屏的 `NewProjectForm` 会渲染它。 */
  error: string | null;
  booting: boolean;
  /** 模板库（`/schema.templates`）：画廊的数据源；空表退化成纯表单。 */
  templates: TemplateView[];
  /** 从模板库「用这个建项目」带回来的预选项（`06` §2.3）；null = 用既有默认。 */
  initialTemplateId?: string | null;
  /** 模板库入口（`06` §2.4 §1）：与 TopBar 模板按钮同一动作（`setView("templates")`）。 */
  onManageTemplates: () => void;
}) {
  const [active, setActive] = useState("home-recent");
  useEffect(() => {
    // jsdom / 非浏览器环境没有 IntersectionObserver：没有就不高亮，不炸。
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) setActive(entry.target.id);
        }
      },
      { rootMargin: "-40% 0px -55% 0px" },
    );
    for (const id of ["home-new", "home-recent"]) {
      const el = document.getElementById(id);
      if (el !== null) observer.observe(el);
    }
    return () => observer.disconnect();
  }, []);
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
          initialTemplateId={initialTemplateId}
        />
      </div>
    );
  }

  return (
    <div className="doc__inner home home--split">
      <HomeNav
        active={active === "home-new" ? "new" : "recent"}
        onTemplates={onManageTemplates}
      />
      <div className="home__main">
        <header className="home__head">
          {/* 页面标识：40px primary 方块里放同一个 Logo。不用状态色——
              缺口/矛盾/开放是语义色，四处必须同一个颜色，挪用去做装饰就破了那条纪律。 */}
          <span className="home__mark" aria-hidden="true">
            <Logo />
          </span>
          <div className="home__heading">
            <h1 className="home__title">文档中心</h1>
            <p className="home__lede">找到你的产品文档，接着上次的想法继续写。</p>
          </div>
          <button type="button" className="btn btn--primary home__new" onClick={() => document.getElementById("new-project-title")?.focus()}>
            + 新建项目
          </button>
        </header>

        {/* **不摆模型名与席位轮上限**：那是运行信息，属于设置与调试。一个"资料库"
            页头写着 openai/qwen3.7-flash 会让人以为该在这里换模型。
            清单由后端 `/health` 与 `/schema.limits` 给出，不编。 */}
        <p className="home__meta">
          {projects.length} 个项目 · 最近更新 {newestOf(projects) ?? "—"}
        </p>

        <div className="home__content">
          <section id="home-recent" className="home__panel" aria-label="最近项目">
            <header className="home__panel-head">
              <h2 className="home__section-title">最近项目</h2>
              <p className="home__meta">按最近更新排序 · 点击项目打开文档</p>
            </header>
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
                    <span className="projects__title" title={item.title}>{item.title}</span>
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
          <section id="home-new" className="home__panel home__create" aria-label="新建项目">
            <header className="home__panel-head">
              <h2 className="home__section-title">新建项目</h2>
              <p className="home__meta">选一套文档骨架，再给产品起个名字。</p>
            </header>
            <NewProjectForm
              templates={templates}
              onCreate={onCreate}
              pending={pending}
              error={error}
              initialTemplateId={initialTemplateId}
            />
            <button type="button" className="home__navlink" onClick={onManageTemplates}>管理模板 →</button>
          </section>
        </div>
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
