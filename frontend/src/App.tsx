/* 界面装配：外壳 + 三栏 + 底部常驻条。
   业务判断一条都不在这里——"该弹什么"由 awaitingMode 从后端投影里读出来，
   "能做什么"由后端肯不肯接决定。这个文件只负责把数据摆到位置上、把动作递出去。 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { buildEvidenceIndex } from "./api/evidence";
import { awaitingMode, buildAnswers, buildOpeningBody } from "./api/payload";
import { BottomStrip } from "./components/BottomStrip";
import { DocumentPane } from "./components/DocumentPane";
import { EditScope } from "./components/EditContext";
import { EvidenceScope } from "./components/EvidenceContext";
import { jumpToEvidence } from "./components/jump";
import { LoopColumn } from "./components/LoopColumn";
import { Onboarding } from "./components/Onboarding";
import { SideRail } from "./components/SideRail";
import { Banner, Skeleton } from "./components/States";
import { TopBar } from "./components/TopBar";
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
  const [activeSection, setActiveSection] = useState("s1");
  const [pendingScroll, setPendingScroll] = useState<string | null>(null);

  const mode = awaitingMode(round);
  const project = state.project;
  const drafts = useRoundDrafts(round, mode);

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
  const jumpToEvidenceById = useCallback((eid: string) => {
    void jumpToEvidence(eid);
  }, []);

  /** 点树上的一个节点：切到它所在的那一章，再滚到它。 */
  const openNode = useCallback((sectionId: string, targetId: string) => {
    setActiveSection(sectionId);
    setPendingScroll(
      targetId.startsWith("block-") ? targetId : `block-${targetId}`,
    );
  }, []);

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
            theme={theme}
            onToggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")}
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
          />

          <LoopColumn
            round={round}
            doc={doc}
            mode={mode}
            pending={data.pending}
            drafts={drafts}
            onSubmitOpening={() =>
              round !== null &&
              void data.submitOpening(
                round.round_id,
                buildOpeningBody(round, drafts.seats, drafts.edits),
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
              void data.confirm(round.round_id, drafts.accepted)
            }
            onRetry={() => {
              if (round !== null) void data.retry(round.round_id);
            }}
          />

          <BottomStrip
            mode={mode}
            pending={data.pending}
            drawerOpen={
              !drafts.collapsed &&
              (mode === "questionnaire" || mode === "draft") &&
              round !== null
            }
            onStartRound={(input) => void data.startRound(input)}
            onOpenDrawer={() => drafts.setCollapsed(false)}
            onRetry={() => {
              if (round !== null) void data.retry(round.round_id);
            }}
          />
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
