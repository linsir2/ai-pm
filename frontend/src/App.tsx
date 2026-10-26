/* 界面装配：外壳 + 三栏 + 底部常驻条。
   业务判断一条都不在这里——"该弹什么"由 awaitingMode 从后端投影里读出来，
   "能做什么"由后端肯不肯接决定。这个文件只负责把数据摆到位置上、把动作递出去。 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { buildEvidenceIndex } from "./api/evidence";
import { ApiError, api } from "./api/client";
import type { SearchHitView } from "./api/types";
import {
  awaitingMode,
  buildAnswers,
  buildOpeningBody,
  materialHitNotice,
  materialHitTarget,
  searchHitAction,
} from "./api/payload";
import type { ExplorationAnchor } from "./api/payload";
import { discussionTopic } from "./state/discussions";
import type { DiscussionAnchor } from "./state/discussions";
import { loopBadgeCount, needsLoopAttention } from "./state/progress";
import { DocumentPane } from "./components/DocumentPane";
import { TemplateLibraryScreen } from "./templates/TemplateLibraryScreen";
import { EditScope } from "./components/EditContext";
import { EvidenceScope } from "./components/EvidenceContext";
import { DiscussionScope } from "./components/DiscussionContext";
import { ExplorationScope } from "./components/ExplorationContext";
import { jumpToChunk, jumpToEntry, jumpToEvidence } from "./components/jump";
import { featureFieldTarget, featureHome, resolveTarget, sectionOfEntry } from "./document/structure";
import { AdminView } from "./components/AdminView";
import { LoopColumn } from "./components/LoopColumn";
import type { LoopTab } from "./components/LoopColumn";
import { MaterialLibrary } from "./components/MaterialLibrary";
import { MemoryDrawer } from "./components/MemoryDrawer";
import { Onboarding } from "./components/Onboarding";
import { ProjectList } from "./components/ProjectList";
import { ReadinessDrawer } from "./components/ReadinessDrawer";
import { ResearchDrawer } from "./components/ResearchDrawer";
import { SideRail } from "./components/SideRail";
import { Banner, Skeleton } from "./components/States";
import { LoginView } from "./components/LoginView";
import { TopBar } from "./components/TopBar";
import { VersionDrawer } from "./components/VersionDrawer";
import { AuthProvider, useAuth } from "./state/auth";
import { useRoundDrafts } from "./state/useRoundDrafts";
import { useWorkspaceData } from "./state/useWorkspaceData";

const THEME_KEY = "pmstudio-theme";

/* 装配点：Provider 在外，守卫在内。未登录（或身份还没读回来）时**不挂载工作区**——
   否则 useWorkspaceData 的启动 effect 会在未登录时报一堆 401，登录后又不重跑。 */
export default function App() {
  return (
    <AuthProvider>
      <ThemeGate />
      <AppRoot />
    </AuthProvider>
  );
}

/**
 * 主题在**登录之前**就要落到 `<html>` 上。
 *
 * 主题 state 住在 `Workspace`，而 `Workspace` 登录后才渲染——所以原本只有登录之后
 * `data-theme` 才被写下去：设了暗色的人登出（或者压根还没登进去）看到的是一片亮屏。
 * 这里只读一次 localStorage 把存量主题先铺上；真正的切换开关仍在 `Workspace` 里
 * （登录屏不放开关：主题只有一个来源，别造第二个真相源）。
 */
function ThemeGate() {
  useEffect(() => {
    if (window.localStorage.getItem(THEME_KEY) === "dark") {
      document.documentElement.dataset.theme = "dark";
    }
  }, []);
  return null;
}

function AppRoot() {
  const { user, ready } = useAuth();
  if (!ready) {
    return (
      <div className="shell shell--plain">
        <main className="doc" aria-label="正在读取身份">
          <div className="doc__inner">
            <Skeleton lines={3} label="正在读身份" />
          </div>
        </main>
      </div>
    );
  }
  // 守卫：无 user 即登录页，workspace / projects / templates / admin 一个都不渲染。
  if (user === null) {
    return <LoginView />;
  }
  return <Workspace />;
}

function Workspace() {
  const { user, logout } = useAuth();
  const data = useWorkspaceData();
  const { state, projects } = data;
  const round = state.round;
  const doc = state.document;

  const [theme, setTheme] = useState<"light" | "dark">(() =>
    window.localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light",
  );
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [readinessOpen, setReadinessOpen] = useState(false);
  const [memoryOpen, setMemoryOpen] = useState(false);
  // 发起调研的抽屉（M8）：点条目的「调研」先落到这里，再点「开始调研」才真发请求。
  const [researchAnchor, setResearchAnchor] = useState<ExplorationAnchor | null>(null);
  // 问卷题行上的「聊聊」（M9）：点一下只把题面与"问的人"放这儿，切到讨论 tab 才开聊。
  const [discussionAnchor, setDiscussionAnchor] = useState<DiscussionAnchor | null>(null);
  // 闭环栏页签（M13 从 LoopColumn 上提到这里）：文档区的「讨论」入口要能跨栏切过去
  const [loopTab, setLoopTab] = useState<LoopTab>("round");
  /** 模拟抽屉开没开（M10）：artifact 在 round.sim_inquiry 里，开关只管显隐。 */
  const [simOpen, setSimOpen] = useState(false);
  /** 评测抽屉开没开（M11）：artifact 在 round.sim_review 里，开关只管显隐。 */
  const [reviewOpen, setReviewOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("s1");
  const [pendingScroll, setPendingScroll] = useState<string | null>(null);
  /** 顶层视图：项目列表 ⇄ 工作区。**纯 UI 态，不进 URL、不进后端**——
      `?project=` 才是要留着刷新恢复的那一个（两者性质不同，混在一起就要回答
      "刷新时有 view=projects 没 project 该去哪"）。 */
  /** 文档列开着没（想看才打开；默认开，顶栏可收）。纯 UI 态。 */
  const [docOpen, setDocOpen] = useState(true);
  /** 右栏开着没（06 §2.4 §3）：null = 按判据初判，点 rail/收起后记 true/false 手动覆盖。
      纯 UI 态，不持久化、不进 URL/后端。 */
  const [loopOpen, setLoopOpen] = useState<boolean | null>(null);
  /** 顶层视图：工作区 ⇄ 项目列表 ⇄ 模板库（`06` §2.3）。**纯 UI 态，不进 URL、不进后端**——
      `?project=` 才是要留着刷新恢复的那一个（两者性质不同，混在一起就要回答
      "刷新时有 view=projects 没 project 该去哪"）。
      用枚举而不是两个 boolean：两个 boolean 表达互斥视图迟早会出第四种组合。 */
  const [view, setView] = useState<"workspace" | "projects" | "templates" | "admin">(
    "workspace",
  );
  /** 从模板库点「用这个建项目」带回来的模板（null = 不预选，回到既有默认）。 */
  const [pickedTemplateId, setPickedTemplateId] = useState<string | null>(null);
  const listing = view === "projects";

  const mode = awaitingMode(round);
  /** 右栏展开条件（06 §2.4 §3）：nodoc 优先（文档都没开还谈什么右栏），否则按它选 shell / shell--noloop。 */
  const loopExpanded = loopOpen ?? needsLoopAttention(round);
  const project = state.project;
  const drafts = useRoundDrafts(round, mode);
  // 点跨轮编号时会切到那一轮（只读）；其余时候看的都是当前这一轮
  const shownRound = data.historyRound ?? round;
  const readingHistory = data.historyRound !== null;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  // 切项目：把**属于某个项目**的 UI 态清干净。数据侧由 `useWorkspaceData.selectProject`
  // 负责（它清自己那份 useState）；这里的开关与锚点活在 App，得自己清——
  // 尤其是 `researchAnchor` / `discussionAnchor`：它们带着上一个项目的原文，
  // 不清就会拿旧文字在新项目里发起动作。`theme` / `view` / `docOpen` 是全局或布局偏好，不动。
  useEffect(() => {
    setVersionsOpen(false);
    setLibraryOpen(false);
    setReadinessOpen(false);
    setMemoryOpen(false);
    setResearchAnchor(null);
    setDiscussionAnchor(null);
    setLoopTab("round");
    setSimOpen(false);
    setReviewOpen(false);
    setActiveSection("s1");
    setPendingScroll(null);
    setLoopOpen(null);
  }, [state.projectId]);

  // 从功能子树点进来：先切到 s5，等它渲染出来再滚过去。
  useEffect(() => {
    if (pendingScroll === null) return;
    const target = document.getElementById(pendingScroll);
    target?.scrollIntoView({ block: "start", behavior: "smooth" });
    setPendingScroll(null);
  }, [pendingScroll, activeSection]);

  const evidence = useMemo(() => buildEvidenceIndex(round), [round]);
  /** 点编号：先看本轮索引（快）→ 再问后端（跨轮 / 被谁用着）→ 能跳轮就跳轮，能滚内容就滚内容。 */
  const jumpToEvidenceById = useCallback(
    (eid: string) => {
      if (jumpToEvidence(eid)) return;
      const projectId = state.projectId;
      if (projectId === null) return;
      void (async () => {
        try {
          const hit = await api.evidence(projectId, eid);
          const entryId = hit.used_by[0];
          if (entryId !== undefined && doc !== null) {
            const sectionId = sectionOfEntry(doc, entryId);
            if (sectionId !== null) {
              setActiveSection(sectionId);
              setPendingScroll(`entry-${entryId}`);
              window.setTimeout(() => void jumpToEntry(entryId), 320);
              return;
            }
          }
          if (hit.round_id !== null && hit.round_id !== shownRound?.round_id) {
            await data.viewRound(hit.round_id);
            return;
          }
          data.notify(`出处 ${eid}（${hit.kind_label}）：${hit.summary} —— 还没有落到任何内容里`);
        } catch (error) {
          data.notify(error instanceof ApiError ? error.message : String(error));
        }
      })();
    },
    [data, doc, shownRound, state.projectId],
  );

  // 建项目成功就进工作区（createProject 成功返回 summary，失败返回 null）。
  const enterCreated = useCallback(
    (title: string, templateId: string) =>
      void data
        .createProject(title, templateId)
        .then((summary) => {
          if (summary !== null) {
            setView("workspace");
            setPickedTemplateId(null);
          }
        }),
    [data],
  );

  const templates = data.schema?.templates ?? [];

  /** 点树上的一个节点：切到它所在的那一章，再滚到它。 */
  const openNode = useCallback((sectionId: string, targetId: string) => {
    setActiveSection(sectionId);
    setPendingScroll(
      targetId.startsWith("block-") ? targetId : `block-${targetId}`,
    );
  }, []);

  /** 点去处（问卷/草案）：跳到文档那一条；文档列收着就展开；指不到位置就说实话。 */
  const openTarget = useCallback(
    (label: string) => {
      const hit = resolveTarget(doc, label);
      if (hit === null) {
        data.notify(`这条去处现在指不到文档里的位置（${label}）`);
        return;
      }
      setDocOpen(true);
      setActiveSection(hit.sectionId);
      if (hit.sectionId === "preference") {
        setPendingScroll("sec-preference");
        return;
      }
      if (hit.blockId !== null) setPendingScroll(`block-${hit.blockId}`);
    },
    [doc, data],
  );

  /** 点搜索结果（F6）：按命中类型各走各的跳法——条目 / 功能 / 编号 / 席位发言。 */
  const pickSearchHit = useCallback(
    (hit: SearchHitView) => {
      if (hit.kind === "opinion") {
        void data.viewRound(hit.ref);
        return;
      }
      if (hit.kind === "evidence") {
        jumpToEvidenceById(hit.ref);
        return;
      }
      if (hit.kind === "feature") {
        // 跳到功能**实际所在**的那一章（模板锚点，不是 s5）
        const home = featureHome(doc);
        if (home === null) {
          data.notify("这份文档里还没有功能，跳不过去");
          return;
        }
        setActiveSection(home);
        setPendingScroll(`block-${hit.ref}`);
        return;
      }
      if (hit.kind === "material") {
        // 材料命中：只有"这一块已经带进某一轮"才跳得动（跳的是**当前正在看的那一轮**的材料卡）
        const target = materialHitTarget(hit.ref, shownRound?.materials ?? []);
        if (target.found && jumpToChunk(hit.ref)) return;
        data.notify(
          materialHitNotice(
            hit.chunk_id ?? hit.ref,
            shownRound?.materials ?? [],
            shownRound?.manual_chunks ?? [],
          ),
        );
        return;
      }
      const sectionId = doc === null ? null : sectionOfEntry(doc, hit.ref);
      if (sectionId !== null) {
        setActiveSection(sectionId);
        setPendingScroll(`entry-${hit.ref}`);
        window.setTimeout(() => void jumpToEntry(hit.ref), 320);
        return;
      }
      data.notify("这条内容现在不在文档里（可能在历史版本里）");
    },
    [data, doc, jumpToEvidenceById],
  );

  const addSearchHitToRound = useCallback(
    (hit: SearchHitView) => {
      void data.addHitToRound(hit);
    },
    [data],
  );

  // 手改：三条动作都走同一个入口，改完直接用后端返回的那份文档刷新（不猜）。
  const editApi = useMemo(
    () => ({
      canEdit: project !== null && data.pending === null,
      busy: data.pending === "edit",
      edit: (blockId: string, entryId: string, text: string) =>
        void data.editEntry({
          op: "replace",
          block_id: blockId,
          entry_id: entryId,
          text,
        }),
      add: (blockId: string, text: string) =>
        void data.editEntry({ op: "add", block_id: blockId, text }),
      addField: (featureSlug: string, field: string, text: string) => {
        // 去处取模板的功能锚点（`02` §11.0.69）：发 s5 会在自定义模板下被后端拒
        const target = featureFieldTarget(doc, featureSlug, field);
        if (target === null) {
          data.notify("这份文档里还没有功能章节，这一条加不进去");
          return;
        }
        void data.editEntry({ op: "add", target, text });
      },
      remove: (blockId: string, entryId: string) =>
        void data.editEntry({
          op: "remove",
          block_id: blockId,
          entry_id: entryId,
        }),
    }),
    [data, project],
  );

  // 调研的入口（M8）：条目工具行只负责把锚点交给发起抽屉，不直接发请求。
  const researchApi = useMemo(
    () => ({
      canStart: project !== null && data.pending === null,
      start: (anchor: ExplorationAnchor) => setResearchAnchor(anchor),
    }),
    [project, data.pending],
  );

  // 讨论的入口（M13）：选区把手 / 条目工具行只把**原文**交上来——
  // 归一化与超长截断在这里做一次（`discussionTopic`），然后设锚点并切到讨论页签。
  const discussionApi = useMemo(
    () => ({
      canStart: project !== null && data.pending === null,
      start: (text: string) => {
        setDiscussionAnchor({ topic: discussionTopic(text), role_id: null });
        setLoopTab("discussion");
      },
    }),
    [project, data.pending],
  );

  // 顶层视图：项目列表。项目一个都没有时它内部自己落回 `Onboarding`，
  // 所以这里**不需要**额外的空态分支。
  if (listing) {
    return (
      <div className="shell shell--plain">
        <TopBar
          projects={projects}
          project={null}
          connection={state.connection}
          onSelectProject={data.selectProject}
          onExport={() => void data.exportMarkdown()}
          onHome={() => setView("workspace")}
          onTemplates={() => setView("templates")}
          templatesActive={false}
          theme={theme}
          onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
          user={user ?? undefined}
          onLogout={() => void logout()}
          onAdmin={() => setView("admin")}
        />
        <main className="doc" aria-label="项目列表">
          <ProjectList
            projects={projects}
            onOpen={(projectId) => {
              setView("workspace");
              setPickedTemplateId(null);
              data.selectProject(projectId);
            }}
            onCreate={enterCreated}
            pending={data.pending !== null}
            error={data.bootError}
            booting={data.booting}
            templates={templates}
            initialTemplateId={pickedTemplateId}
            onManageTemplates={() => setView("templates")}
          />
        </main>
      </div>
    );
  }

  // 模板库（`06` §2.3）：编辑态与列表态在同一个视图里切，不另开一层。
  if (view === "templates") {
    return (
      <div className="shell shell--plain">
        <TopBar
          projects={projects}
          project={null}
          connection={state.connection}
          onSelectProject={data.selectProject}
          onExport={() => void data.exportMarkdown()}
          onHome={() => setView("projects")}
          onTemplates={() => setView("templates")}
          templatesActive
          theme={theme}
          onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
          user={user ?? undefined}
          onLogout={() => void logout()}
          onAdmin={() => setView("admin")}
        />
        <main className="doc" aria-label="模板库">
          <TemplateLibraryScreen
            active
            onNavigate={() => setView("projects")}
            onTemplates={() => setView("templates")}
            onUse={(templateId) => {
              // 回项目列表，并把这份模板预选上（"用这个建项目"不许让用户再选一遍）
              setPickedTemplateId(templateId);
              setView("projects");
            }}
          />
        </main>
      </div>
    );
  }

  // 管理后台（Task 8）：权限判定在 `AdminView` 里做（非 admin 渲染"无权限"），
  // 这里只按 view 摆位——与项目列表 / 模板库同一层，不挂工作区。
  if (view === "admin") {
    return (
      <div className="shell shell--plain">
        <TopBar
          projects={projects}
          project={null}
          connection={state.connection}
          onSelectProject={data.selectProject}
          onExport={() => void data.exportMarkdown()}
          onHome={() => setView("projects")}
          onTemplates={() => setView("templates")}
          theme={theme}
          onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
          user={user ?? undefined}
          onLogout={() => void logout()}
          onAdmin={() => setView("admin")}
        />
        <AdminView />
      </div>
    );
  }

  if (project === null) {
    return (
      <div className="shell shell--plain">
        <TopBar
          projects={projects}
          project={null}
          connection={state.connection}
          onSelectProject={data.selectProject}
          onExport={() => void data.exportMarkdown()}
          onTemplates={() => setView("templates")}
          theme={theme}
          onToggleTheme={() => setTheme(theme === "light" ? "dark" : "light")}
          user={user ?? undefined}
          onLogout={() => void logout()}
          onAdmin={() => setView("admin")}
        />
        <main className="doc" aria-label="产品文档">
          <div className="doc__inner">
            {data.booting ? (
              <Skeleton lines={6} label="正在读项目列表" />
            ) : (
              <Onboarding
                onCreate={enterCreated}
                error={data.bootError}
                pending={data.pending !== null}
                templates={templates}
              />
            )}
          </div>
        </main>
      </div>
    );
  }

  return (
    <EvidenceScope index={evidence} onJump={jumpToEvidenceById}>
      <EditScope value={editApi}>
        <ExplorationScope value={researchApi}>
        <DiscussionScope value={discussionApi}>
        <div className={docOpen ? (loopExpanded ? "shell" : "shell shell--noloop") : (loopExpanded ? "shell shell--nodoc" : "shell shell--nodoc shell--noloop")}>
          <TopBar
            projects={projects}
            project={project}
            connection={state.connection}
            onSelectProject={data.selectProject}
            onExport={() => void data.exportMarkdown()}
            onHome={() => setView("projects")}
            docOpen={docOpen}
            onToggleDoc={() => setDocOpen((open) => !open)}
            libraryCount={data.materials.length}
            onOpenLibrary={() => setLibraryOpen(true)}
            theme={theme}
            onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
            user={user ?? undefined}
            onLogout={() => void logout()}
            onAdmin={() => setView("admin")}
            search={
              project === null
                ? null
                : {
                    query: data.searchQuery,
                    results: data.searchResults,
                    pending: data.searchPending,
                    onQueryChange: data.setSearchQuery,
                    onPick: pickSearchHit,
                    actionFor: (hit) =>
                      searchHitAction(hit, round, readingHistory),
                    onAddToRound: addSearchHitToRound,
                  }
            }
          />

          <SideRail
            document={doc}
            round={round}
            readiness={state.readiness}
            activeSection={activeSection}
            onSelectSection={setActiveSection}
            onSelectNode={openNode}
          />

          <DocumentPane
            document={doc}
            project={project}
            round={round}
            justWritten={state.justWritten}
            activeSection={activeSection}
            readiness={state.readiness}
            memoryCount={(state.memory ?? []).length}
            schema={data.schema}
            onOpenVersions={() => setVersionsOpen(true)}
            onOpenReadiness={() => setReadinessOpen(true)}
            onOpenMemory={() => setMemoryOpen(true)}
          />

          <LoopColumn
            tab={loopTab}
            onTabChange={setLoopTab}
            round={shownRound}
            doc={doc}
            schema={data.schema}
            rounds={data.rounds}
            collapsed={!loopExpanded}
            badgeCount={loopBadgeCount(round)}
            onExpand={() => setLoopOpen(true)}
            onCollapse={() => setLoopOpen(false)}
            onViewRound={(roundId) => void data.viewRound(roundId)}
            mode={readingHistory ? null : mode}
            pending={data.pending}
            drafts={drafts}
            explorations={data.explorations}
            explorationsPending={data.explorationsPending}
            onStartResearch={() => setResearchAnchor({ kind: "document" })}
            onRetryExploration={(explorationId) => void data.retryExploration(explorationId)}
            onAbandonExploration={(explorationId) =>
              void data.abandonExploration(explorationId)
            }
            onKeepSeed={(explorationId, candidate) => {
              void data.keepCandidate(explorationId, candidate.candidate_id, "seed");
            }}
            onKeepMaterial={(explorationId, candidate) =>
              void data.keepCandidate(explorationId, candidate.candidate_id, "material")
            }
            onDismissCandidate={(explorationId, candidate) =>
              void data.dismissCandidate(explorationId, candidate.candidate_id)
            }
            onKeepCard={(threadId, cardId, as) =>
              void data.keepDiscussionCard(threadId, cardId, as)
            }
            onDismissCard={(threadId, cardId) =>
              void data.dismissDiscussionCard(threadId, cardId)
            }
            onVerifyCard={(statement) => void data.verifyCard(statement)}
            discussions={data.discussions}
            discussionsPending={data.discussionsPending}
            discussionAnchor={discussionAnchor}
            onStartDiscussion={(start, runInquiry) => data.startPanel(start, runInquiry)}
            onRunPanelRound={(threadId, signal) => data.runPanelRound(threadId, signal)}
            onJoinPanel={(threadId, roleId) => data.joinPanel(threadId, roleId)}
            onAbortPanelRun={(threadId) => data.abortPanelRun(threadId)}
            onSayToThread={(threadId, text) => void data.sayToThread(threadId, text)}
            onAssembleCards={(threadId) => void data.assembleCards(threadId)}
            onKeepDiscussionCard={(threadId, cardId, as) =>
              void data.keepDiscussionCard(threadId, cardId, as)
            }
            onDismissDiscussionCard={(threadId, cardId) =>
              void data.dismissDiscussionCard(threadId, cardId)
            }
            onCloseDiscussion={(threadId) => void data.closeDiscussion(threadId)}
            onDiscussQuestion={setDiscussionAnchor}
            onOpenTarget={openTarget}
            history={
              readingHistory && data.historyRound !== null
                ? { roundId: data.historyRound.round_id, onBack: data.backToLatest }
                : null
            }
            approvedRequestIds={drafts.approvedRequestIds}
            allowWeb={drafts.allowWeb}
            onToggleWebRequest={drafts.toggleWebRequest}
            onToggleAllowWeb={drafts.setAllowWeb}
            onDecideWebRequest={(requestId, decision) => {
              if (round !== null) {
                data.decideWebRequest(round.round_id, requestId, decision);
              }
            }}
            pendingWebRequest={data.pendingWebRequest}
            onSubmitOpening={() =>
              round !== null &&
              void data.submitOpening(
                round.round_id,
                buildOpeningBody(
                  round,
                  drafts.seats,
                  drafts.edits,
                  drafts.webQueries,
                  drafts.approvedRequestIds,
                  drafts.allowWeb,
                ),
              )
            }
            onSubmitAnswers={() =>
              round !== null &&
              void data.submitAnswers(
                round.round_id,
                buildAnswers(round, drafts.answers),
              )
            }
            onSkipAll={() =>
              round !== null &&
              void data.submitAnswers(round.round_id, {
                form_id: round.form_id ?? "",
                answers: [],
                skipped: round.questions.map(
                  (question) => question.question_id,
                ),
              })
            }
            onConfirm={() =>
              round !== null &&
              void data.confirm(round.round_id, drafts.accepted, drafts.memoryAccepted)
            }
            onDig={() => {
              if (round !== null) void data.dig(round.round_id);
            }}
            onSimInquiry={() => {
              if (round !== null) {
                setSimOpen(true);
                void data.runSimInquiry(round.round_id);
              }
            }}
            simOpen={simOpen}
            simPending={data.pending === "simulation"}
            onSimReview={() => {
              if (round !== null) setReviewOpen(true);
            }}
            reviewOpen={reviewOpen}
            reviewPending={data.pending === "sim_review"}
            onRunReview={(personaIds, featureSlugs) => {
              if (round !== null) {
                void data.runSimReview(round.round_id, personaIds, featureSlugs);
              }
            }}
            onCloseReview={() => setReviewOpen(false)}
            onKeepSim={(ids) => {
              if (round !== null) void data.keepSimInquiry(round.round_id, ids);
            }}
            onCloseSim={() => setSimOpen(false)}
            onRetry={() => {
              if (round !== null) void data.retry(round.round_id);
            }}
            onAbandon={() => {
              if (shownRound !== null) void data.abandon(shownRound.round_id);
            }}
            onSkipSeat={(seatId) => {
              if (shownRound !== null) void data.skipSeat(shownRound.round_id, seatId);
            }}
            composerProjectId={project.project_id}
            composerMode={mode}
            composerPending={data.pending}
            composerValue={data.roundInput}
            composerOnChange={data.setRoundInput}
            composerOnStartRound={() => void data.startRound()}
            composerPendingSeedCount={data.pendingSeeds.length}
            composerMaterials={data.pendingMaterials}
            composerOnMaterialUploaded={data.addMaterial}
            composerOnMaterialRemove={data.removeMaterial}
            composerLibraryCount={data.materials.length}
            composerOnOpenLibrary={() => setLibraryOpen(true)}
          />

          {versionsOpen && (
            <div className="overlay-slot">
              <VersionDrawer
                projectId={project.project_id}
                current={doc?.version_no ?? project.version_no}
                onRollback={data.rollback}
                onCollapse={() => setVersionsOpen(false)}
              />
            </div>
          )}

          {libraryOpen && (
            <div className="overlay-slot">
              <MaterialLibrary
                materials={data.materials}
                selected={data.pendingMaterials.map((item) => item.material_id)}
                pending={data.pending !== null}
                onToggleSelected={data.toggleMaterial}
                onDelete={(materialId) => {
                  if (window.confirm("删掉这份材料？它没被引用过，删了就没了（不可恢复）。")) {
                    void data.deleteMaterial(materialId);
                  }
                }}
                onReparse={(materialId) => void data.reparseMaterial(materialId)}
                onCollapse={() => setLibraryOpen(false)}
              />
            </div>
          )}

          {readinessOpen && (
            <div className="overlay-slot">
              <ReadinessDrawer
                items={state.readiness}
                document={doc}
                onCollapse={() => setReadinessOpen(false)}
              />
            </div>
          )}

          {memoryOpen && (
            <div className="overlay-slot">
              <MemoryDrawer
                memory={state.memory ?? []}
                pending={data.pending === "memory"}
                projectId={project.project_id}
                onDelete={(memoryId) => void data.forgetMemory(memoryId)}
                onCollapse={() => setMemoryOpen(false)}
              />
            </div>
          )}

          {researchAnchor !== null && (
            <div className="overlay-slot">
              <ResearchDrawer
                anchor={researchAnchor}
                pending={data.explorationsPending}
                onStart={() => {
                  const anchor = researchAnchor;
                  setResearchAnchor(null);
                  void data.research(anchor);
                }}
                onCancel={() => setResearchAnchor(null)}
              />
            </div>
          )}
        </div>

        {state.error !== null && (
          <div className="toast">
            <Banner>
              {state.error}
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                onClick={data.dismissError}
              >
                知道了
              </button>
            </Banner>
          </div>
        )}
        </DiscussionScope>
        </ExplorationScope>
      </EditScope>
    </EvidenceScope>
  );
}
