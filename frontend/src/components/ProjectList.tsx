/* 项目列表：一个项目一行，点它进那个项目的工作区。

**为什么是"顶层视图"而不是一条路由**：本仓前端零路由依赖（三栏工作区是"一个页面"的
心智），而 `useWorkspaceData` 早就在用 `window.history.replaceState` + `?project=` 记项目。
再引一个路由库要改 App 顶层结构、要处理刷新恢复——为这一屏付那个代价不值。

**一个项目都没有时不画空列表**：直接复用 `Onboarding`。空态只有一份，
两份空态就会漂移（"先建一个项目"那句话改一处忘一处）。

**这一屏一个业务判断都不做**（`App.tsx` 的既有纪律）：行上只有后端 `GET /projects`
真给的东西——`title` / `version_no` / `updated_at`。就绪度**不显示**：那个端点
（`GET /readiness`）的 delivery 面已按 F12 砍掉，为一个列表页给每项多调一次
`/workspace` 是 N+1，而且 `06-前端.md` §1 明令"没有的端点就不做那个功能"。 */

import { useState } from "react";

import type { ProjectSummaryView } from "../api/types";
import { Banner, Skeleton } from "./States";
import { Onboarding } from "./Onboarding";

export function ProjectList({
  projects,
  onOpen,
  onCreate,
  pending,
  error,
  booting,
}: {
  projects: ProjectSummaryView[];
  onOpen: (projectId: string) => void;
  onCreate: (title: string) => void;
  pending: boolean;
  /** 建项目失败时的那一句：只有自己这一屏的 `CreateProject` 会渲染它。 */
  error: string | null;
  booting: boolean;
}) {
  if (booting) {
    return (
      <div className="doc__inner">
        <Skeleton lines={5} label="正在读项目列表" />
      </div>
    );
  }

  if (projects.length === 0) {
    // 空态复用同一个组件：表单、说明文、错误横幅全都是一份
    return (
      <div className="doc__inner">
        <Onboarding onCreate={onCreate} error={error} pending={pending} />
      </div>
    );
  }

  return (
    <div className="doc__inner">
      <header className="projects__head">
        <h1 className="empty__title">你的项目</h1>
        <p className="empty__text">
          一个项目 = 一份 13 章的产品文档骨架。点一行回去接着写；也可以另开一个。
        </p>
      </header>

      <CreateProject onCreate={onCreate} pending={pending} error={error} />

      <ul className="projects" role="list">
        {projects.map((item) => (
          <li key={item.project_id} className="projects__item">
            <button
              type="button"
              className="projects__row"
              onClick={() => onOpen(item.project_id)}
            >
              <span className="projects__title">{item.title}</span>
              <span className="projects__meta mono">
                <span className="projects__version">v{item.version_no}</span>
                <span className="projects__time">{item.updated_at.slice(0, 10)}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
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
