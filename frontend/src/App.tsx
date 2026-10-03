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
  materialHitTarget,
} from "./api/payload";
import { BottomStrip } from "./components/BottomStrip";
import { DocumentPane } from "./components/DocumentPane";
import { EditScope } from "./components/EditContext";
import { EvidenceScope } from "./components/EvidenceContext";
import { jumpToChunk, jumpToEntry, jumpToEvidence } from "./components/jump";
import { sectionOfEntry } from "./document/structure";
import { LoopColumn } from "./components/LoopColumn";
import { MaterialLibrary } from "./components/MaterialLibrary";
import { MemoryDrawer } from "./components/MemoryDrawer";
import { Onboarding } from "./components/Onboarding";
import { ReadinessDrawer } from "./components/ReadinessDrawer";
import { SideRail } from "./components/SideRail";
import { Banner, Skeleton } from "./components/States";
import { TopBar } from "./components/TopBar";
import { VersionDrawer } from "./components/VersionDrawer";
import { useRoundDrafts } from "./state/useRoundDrafts";
import { useWorkspaceData } from "./state/useWorkspaceData";

const THEME_KEY = "pmstudio-theme";

export default function App() {
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
  const [activeSection, setActiveSection] = useState("s1");
  const [pendingScroll, setPendingScroll] = useState<string | null>(null);

  const mode = awaitingMode(round);
  const project = state.project;
  const drafts = useRoundDrafts(round, mode);
  // 点跨轮编号时会切到那一轮（只读）；其余时候看的都是当前这一轮
  const shownRound = data.historyRound ?? round;
  const readingHistory = data.historyRound !== null;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

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

  /** 点树上的一个节点：切到它所在的那一章，再滚到它。 */
  const openNode = useCallback((sectionId: string, targetId: string) => {
    setActiveSection(sectionId);
    setPendingScroll(
      targetId.startsWith("block-") ? targetId : `block-${targetId}`,
    );
  }, []);

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
        setActiveSection("s5");
        setPendingScroll(`block-${hit.ref}`);
        return;
      }
      if (hit.kind === "material") {
        // 材料命中：只有"这一块已经带进某一轮"才跳得动（跳的是**当前正在看的那一轮**的材料卡）
        const target = materialHitTarget(hit.ref, shownRound?.materials ?? []);
        if (target.found && jumpToChunk(hit.ref)) return;
        data.notify(
          target.found
            ? `这一块在「${target.title}」（${target.anchor}），但它现在不在页面上——切到它所在的那一轮再看。`
            : "这一块还没带进任何一轮（所以它没有编号）；它在材料列表里，等它进某一轮才会发 E 编号。",
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
      addField: (featureSlug: string, field: string, text: string) =>
        void data.editEntry({
          op: "add",
          target: { zone: "body", section_id: "s5", feature_slug: featureSlug, field },
          text,
        }),
      remove: (blockId: string, entryId: string) =>
        void data.editEntry({
          op: "remove",
          block_id: blockId,
          entry_id: entryId,
        }),
    }),
    [data, project],
  );

  if (project === null) {
    return (
      <div className="shell shell--plain">
        <TopBar
          projects={projects}
          project={null}
          document={null}
          readiness={[]}
          roundStep={null}
          connection={state.connection}
          onSelectProject={data.selectProject}
          onExport={() => void data.exportMarkdown()}
          theme={theme}
          onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
        />
        <main className="doc" aria-label="产品文档">
          <div className="doc__inner">
            {data.booting ? (
              <Skeleton lines={6} label="正在读项目列表" />
            ) : (
              <Onboarding
                onCreate={(title) => void data.createProject(title)}
                error={data.bootError}
                pending={data.pending !== null}
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
        <div className="shell">
          <TopBar
            projects={projects}
            project={project}
            document={doc}
            readiness={state.readiness}
            roundStep={round?.step ?? null}
            connection={state.connection}
            onSelectProject={data.selectProject}
            onExport={() => void data.exportMarkdown()}
            onOpenVersions={() => setVersionsOpen(true)}
            onOpenReadiness={() => setReadinessOpen(true)}
            memoryCount={(state.memory ?? []).length}
            onOpenMemory={() => setMemoryOpen(true)}
            theme={theme}
            onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
            search={
              project === null
                ? null
                : {
                    query: data.searchQuery,
                    results: data.searchResults,
                    pending: data.searchPending,
                    onQueryChange: data.setSearchQuery,
                    onPick: pickSearchHit,
                  }
            }
          />

          <SideRail
            projects={projects}
            project={project}
            document={doc}
            round={round}
            activeSection={activeSection}
            onSelectProject={data.selectProject}
            onSelectSection={setActiveSection}
            onSelectNode={openNode}
          />

          <DocumentPane
            document={doc}
            round={round}
            justWritten={state.justWritten}
            activeSection={activeSection}
            readiness={state.readiness}
            schema={data.schema}
          />

          <LoopColumn
            round={shownRound}
            doc={doc}
            schema={data.schema}
            rounds={data.rounds}
            onViewRound={(roundId) => void data.viewRound(roundId)}
            mode={readingHistory ? null : mode}
            pending={data.pending}
            drafts={drafts}
            history={
              readingHistory && data.historyRound !== null
                ? { roundId: data.historyRound.round_id, onBack: data.backToLatest }
                : null
            }
            onSubmitOpening={() =>
              round !== null &&
              void data.submitOpening(
                round.round_id,
                buildOpeningBody(round, drafts.seats, drafts.edits, drafts.webQueries),
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
            onRetry={() => {
              if (round !== null) void data.retry(round.round_id);
            }}
            onAbandon={() => {
              if (shownRound !== null) void data.abandon(shownRound.round_id);
            }}
          />

          <BottomStrip
            projectId={project.project_id}
            mode={mode}
            pending={data.pending}
            drawerOpen={
              !drafts.collapsed &&
              (mode === "questionnaire" || mode === "draft") &&
              round !== null
            }
            materials={data.pendingMaterials}
            onMaterialUploaded={data.addMaterial}
            onMaterialRemove={data.removeMaterial}
            libraryCount={data.materials.length}
            onOpenLibrary={() => setLibraryOpen(true)}
            onStartRound={(input) => void data.startRound(input, data.pendingMaterials)}
            onOpenDrawer={() => drafts.setCollapsed(false)}
            onRetry={() => {
              if (round !== null) void data.retry(round.round_id);
            }}
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
                onDelete={(memoryId) => void data.forgetMemory(memoryId)}
                onCollapse={() => setMemoryOpen(false)}
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
      </EditScope>
    </EvidenceScope>
  );
}
