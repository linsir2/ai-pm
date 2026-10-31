/* 闭环栏（右栏）：上半是「本轮 / 灵感箱 / 讨论 / 研究」页签，中间是**永远在渲染**的
   对话流（LoopPane），需要你决策时（复述 / 问卷 / 草案）以浮层弹出对应卡——
   流在后面继续、可收起留 chip、提交后自动收并落一条记录。
   2026-10-29 改：之前 `mode==="opening"` 会整栏独占、把流整个顶掉，
   长请求期间右栏看起来"卡死"——现在流是主角，卡只是浮在它上面的决策点。 */

import type { AwaitingMode } from "../api/payload";
import type {
  CandidateView,
  DocumentView,
  ExplorationView,
  MaterialSummaryView,
  RoundHeadView,
  RoundView,
  SchemaView,
  ThreadView,
} from "../api/types";
import type { DiscussionAnchor, PanelStart } from "../state/discussions";
import { discussionTurnLimit, roleOptions } from "../state/discussions";
import { drawerChip, drawerFor, pendingDrawer } from "../state/drawer";
import { inboxCount } from "../state/inbox";
import { roundElapsed, roundOrdinal } from "../state/progress";
import type { RoundDrafts } from "../state/useRoundDrafts";
import { DiscussionTray } from "./DiscussionTray";
import { DraftDrawer } from "./DraftDrawer";
import { canConfirmDraft } from "../api/payload";
import { InboxTray } from "./InboxTray";
import { ResearchTray } from "./ResearchTray";
import { LoopComposer } from "./LoopComposer";
import { LoopPane } from "./LoopPane";
import { OpeningCard } from "./OpeningCard";
import { QuestionnaireDrawer } from "./QuestionnaireDrawer";
import { SimInquiryDrawer } from "./SimInquiryDrawer";
import { SimReviewDrawer } from "./SimReviewDrawer";
import { reviewLimits } from "../state/simReview";
import { RoundHistory } from "./RoundHistory";

export type LoopTab = "round" | "tray" | "discussion" | "research";

export function LoopColumn({
  tab,
  onTabChange,
  round,
  doc,
  schema,
  rounds = [],
  mode,
  pending,
  drafts,
  history = null,
  explorations = [],
  explorationsPending = false,
  researches = [],
  researchesPending = false,
  researchTopicLimit,
  researchSourceLimit,
  onStartWebResearch,
  onRetryResearch,
  onAbandonResearch,
  onAttachResearchSources,
  discussions = [],
  discussionsPending = false,
  discussionAnchor = null,
  collapsed = false,
  badgeCount = 0,
  onExpand,
  onCollapse,
  approvedRequestIds,
  allowWeb,
  onToggleWebRequest,
  onToggleAllowWeb,
  onDecideWebRequest,
  onRecoverWebRequest,
  pendingWebRequest,
  onSubmitOpening,
  onSubmitAnswers,
  onSkipAll,
  onConfirm,
  onDig,
  onSimInquiry,
  simOpen,
  simPending,
  onKeepSim,
  onCloseSim,
  onSimReview,
  reviewOpen,
  reviewPending,
  onRunReview,
  onCloseReview,
  onRetry,
  onAbandon,
  onSkipSeat,
  onViewRound,
  onStartResearch,
  onRetryExploration,
  onAbandonExploration,
  onKeepSeed,
  onKeepMaterial,
  onDismissCandidate,
  onKeepCard,
  onDismissCard,
  onVerifyCard,
  onStartDiscussion,
  onRunPanelRound,
  onJoinPanel,
  onAbortPanelRun,
  onSayToThread,
  onAssembleCards,
  onKeepDiscussionCard,
  onDismissDiscussionCard,
  onCloseDiscussion,
  onDiscussQuestion,
  onOpenTarget,
  composerProjectId,
  composerMode,
  composerPending,
  composerValue,
  composerOnChange,
  composerOnStartRound,
  composerPendingSeedCount = 0,
  composerMaterials = [],
  composerOnMaterialUploaded,
  composerOnMaterialRemove,
  composerLibraryCount = 0,
  composerOnOpenLibrary,
}: {
  round: RoundView | null;
  doc: DocumentView | null;
  /** `GET /schema` 的投影：取上限常量与讨论角色（拿不到就由下游回落）。 */
  schema: SchemaView | null;
  /** 这个项目走过的轮次（最新在前）：跨轮的入口（F4）。 */
  rounds?: RoundHeadView[];
  /** 当前页签（M13 起由 App 持有：文档区的「讨论」入口要能跨栏切过来）。 */
  tab: LoopTab;
  onTabChange: (tab: LoopTab) => void;
  /** null = 只看不算数（正在看历史轮：不出开场卡、不出抽屉）。 */
  mode: AwaitingMode | null;
  pending: string | null;
  drafts: RoundDrafts;
  /** 正在看历史轮（只读）时给出的横幅；null = 正常看当前这一轮。 */
  history?: { roundId: string; onBack: () => void } | null;
  /** 过目台的数据源（M8）：这个项目走过的探索。 */
  explorations?: ExplorationView[];
  explorationsPending?: boolean;
  /** 右栏折叠态（06 §2.4 §3）：rail 只是它的折叠态，不另起文件。 */
  collapsed?: boolean;
  /** rail 徽标数：纯函数 `loopBadgeCount` 在调用方算好（组件不做业务判断）。 */
  badgeCount?: number;
  onExpand?: () => void;
  onCollapse?: () => void;
  /** 复述卡上勾中的 analyzer 申请 id（P3a）。 */
  approvedRequestIds: string[];
  /** 后续席位 / 整合者申请是否自动批准（默认关）。 */
  allowWeb: boolean;
  onToggleWebRequest: (requestId: string) => void;
  onToggleAllowWeb: (allow: boolean) => void;
  /** 时间线上的联网申请逐条决策（批准即执行）。 */
  onDecideWebRequest: (requestId: string, decision: "approve" | "reject") => void;
  /** 超额 approved 的逐条恢复（P3b）：执行一次或放弃。 */
  onRecoverWebRequest?: (requestId: string, action: "execute" | "abandon") => void;
  /** 哪一条申请的哪个决策/恢复在途。 */
  pendingWebRequest: {
    requestId: string;
    decision: "approve" | "reject" | "execute" | "abandon";
  } | null;
  onSubmitOpening: () => void;
  onSubmitAnswers: () => void;
  onSkipAll: () => void;
  onConfirm: (roundId: string, draftId: string) => void;
  onDig: () => void;
  /** 草案卡上的"去模拟调研"（M10）：调用方跑完打开模拟抽屉。 */
  onSimInquiry: () => void;
  /** 草案卡上的"模拟评测"：打开选择器（M11）。 */
  onSimReview: () => void;
  /** 模拟抽屉开没开：开关只管显隐，内容从 round.sim_inquiry 来。 */
  simOpen: boolean;
  /** 评测抽屉开没开（M11）。 */
  reviewOpen: boolean;
  simPending: boolean;
  /** 评测在跑（M11）：抽屉显示等待。 */
  reviewPending: boolean;
  onKeepSim: (ids: string[]) => void;
  onCloseSim: () => void;
  onRunReview: (personaIds: string[], featureSlugs: string[]) => void;
  onCloseReview: () => void;
  onRetry: () => void;
  onAbandon: () => void;
  onSkipSeat: (seatId: string) => void;
  /** 切过去只读看某一轮（复用已有的 `viewRound`）。 */
  onViewRound: (roundId: string) => void;
  /** 发起调研（整份文档；讨论区顶部，M17 从灵感箱底部搬过来）。 */
  onStartResearch: () => void;
  onRetryExploration: (explorationId: string) => void;
  onAbandonExploration: (explorationId: string) => void;
  onKeepSeed: (explorationId: string, candidate: CandidateView) => void;
  onKeepMaterial: (explorationId: string, candidate: CandidateView) => void;
  onDismissCandidate: (explorationId: string, candidate: CandidateView) => void;
  /** 箱里收下讨论卡：`seed` = 预填下一轮输入；`material` = 落材料。 */
  onKeepCard: (threadId: string, cardId: string, as: "seed" | "material") => void;
  onDismissCard: (threadId: string, cardId: string) => void;
  /** 拿去验证（M13）：拿卡片原话起一趟聚焦调研（箱与讨论 tab 双处同动作）。 */
  onVerifyCard: (statement: string) => void;
  /** 研究 tab 的数据源（P3b-1）：这个项目跑过的研究（最新在前）。 */
  researches?: ExplorationView[];
  researchesPending?: boolean;
  /** 研究主题字数上限（`/schema.limits`）：输入框与"超长"提示读它。 */
  researchTopicLimit?: number;
  /** 一轮最多挂几张来源卡（`/schema.limits`）：给了就在满额时禁用按钮。 */
  researchSourceLimit?: number;
  onStartWebResearch: (topic: string) => void;
  onRetryResearch: (explorationId: string) => void;
  onAbandonResearch: (explorationId: string) => void;
  onAttachResearchSources: (explorationId: string, eids: string[]) => void;
  /** 讨论 tab 的数据源（M9）：这个项目开过的线程（最新在前）。 */
  discussions?: ThreadView[];
  discussionsPending?: boolean;
  /** 问卷里点「聊聊」带过来的题面与问的人（null = 自由开聊）。 */
  discussionAnchor?: DiscussionAnchor | null;
  onStartDiscussion: (anchor: PanelStart, runInquiry: boolean) => Promise<string | null>;
  onRunPanelRound: (threadId: string, signal?: AbortSignal) => Promise<ThreadView | null>;
  onJoinPanel: (threadId: string, roleId: string) => void;
  onAbortPanelRun: (threadId: string) => void;
  onSayToThread: (threadId: string, text: string) => void;
  onAssembleCards: (threadId: string) => void;
  onKeepDiscussionCard: (threadId: string, cardId: string, as: "seed" | "material") => void;
  onDismissDiscussionCard: (threadId: string, cardId: string) => void;
  onCloseDiscussion: (threadId: string) => void;
  /** 问卷题行上的「聊聊」：只把锚点交上去（不直接发请求）。 */
  onDiscussQuestion: (anchor: DiscussionAnchor) => void;
  /** 问卷/草案里去处的跳转（到文档那一条）：不传就不出可点去处。 */
  onOpenTarget?: (label: string, entryId?: string) => void;
  /** 右底 composer（旧 BottomStrip 整块搬入）：与旧 foot 收到的 mode 同一个值。 */
  composerProjectId: string;
  composerMode: AwaitingMode;
  composerPending: string | null;
  composerValue: string;
  composerOnChange: (text: string) => void;
  composerOnStartRound: () => void;
  composerPendingSeedCount?: number;
  composerMaterials?: MaterialSummaryView[];
  composerOnMaterialUploaded?: (material: MaterialSummaryView) => void;
  composerOnMaterialRemove?: (materialId: string) => void;
  composerLibraryCount?: number;
  composerOnOpenLibrary?: () => void;
}) {
  const showTabs = mode !== "opening";
  const onTray = tab === "tray" && showTabs;
  const onDiscussion = tab === "discussion" && showTabs;
  const onResearch = tab === "research" && showTabs;
  const onRound = !onTray && !onDiscussion && !onResearch;
  // 灵感箱的未处理数：新卡 + 待过目候选（0 条不渲染徽标）
  const inbox = inboxCount(discussions, explorations);
  // 决策卡弹出状态机（state/drawer.ts）：`waiting_on` 是唯一真相源。
  // pendingKind = 现在等的是哪张卡（收起了也要出 chip）；drawerKind = 现在真的弹的。
  const pendingKind = onRound && history === null ? pendingDrawer(round) : null;
  const drawerKind = drawerFor(round, drafts.collapsed);
  const drawerOpen = drawerKind === "questionnaire" || drawerKind === "draft";
  const chip =
    pendingKind !== null && drawerKind === null && round !== null
      ? drawerChip(pendingKind, round)
      : null;
  // 跨轮入口（"这个项目走过 N 轮"）：不进独立一行，进 LoopPane/OpeningCard 头部右端。
  const historyNav = (
    <RoundHistory
      rounds={rounds}
      currentRoundId={round?.round_id ?? null}
      onView={onViewRound}
    />
  );

  // 折叠态：只剩 56px rail 里的一个竖按钮（图标 + 徽标数），其余原样不渲染。
  if (collapsed) {
    return (
      <div className="loop">
        <button
          type="button"
          className="topbar__action"
          onClick={onExpand}
          aria-label="展开闭环区"
        >
          闭环{badgeCount > 0 ? ` · ${badgeCount}` : ""}
        </button>
      </div>
    );
  }

  return (
    <div className="loop">
      {showTabs && (
        <div className="loop__tabs" role="tablist" aria-label="闭环区">
          <button
            type="button"
            role="tab"
            aria-selected={onRound}
            className={onRound ? "loop__tab loop__tab--on" : "loop__tab"}
            onClick={() => onTabChange("round")}
          >
            本轮
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={onTray}
            className={onTray ? "loop__tab loop__tab--on" : "loop__tab"}
            onClick={() => onTabChange("tray")}
          >
            灵感箱
            {inbox > 0 && (
              <span className="loop__tabbadge" aria-hidden="true">
                {inbox}
              </span>
            )}
            {inbox > 0 && <span className="sr-only">，有 {inbox} 条待过目</span>}
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={onDiscussion}
            className={onDiscussion ? "loop__tab loop__tab--on" : "loop__tab"}
            onClick={() => onTabChange("discussion")}
          >
            讨论
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={onResearch}
            className={onResearch ? "loop__tab loop__tab--on" : "loop__tab"}
            onClick={() => onTabChange("research")}
          >
            研究
          </button>
          {onCollapse !== undefined && (
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={onCollapse}
              aria-label="收起闭环区"
            >
              收起
            </button>
          )}
        </div>
      )}

      {onResearch ? (
        <ResearchTray
          runs={researches}
          pending={researchesPending ?? false}
          topicLimit={researchTopicLimit}
          sourceLimit={researchSourceLimit}
          round={round}
          onStart={onStartWebResearch}
          onRetry={onRetryResearch}
          onAbandon={onAbandonResearch}
          onAttach={onAttachResearchSources}
        />
      ) : onDiscussion ? (
        <DiscussionTray
          threads={discussions}
          roles={roleOptions(schema)}
          turnLimit={discussionTurnLimit(schema?.limits)}
          anchor={discussionAnchor}
          pending={discussionsPending}
          onStartPanel={onStartDiscussion}
          onRunRound={onRunPanelRound}
          onJoin={onJoinPanel}
          onAbortRun={onAbortPanelRun}
          onSay={onSayToThread}
          onCards={onAssembleCards}
          onKeep={onKeepDiscussionCard}
          onDismiss={onDismissDiscussionCard}
          onVerify={onVerifyCard}
          onClose={onCloseDiscussion}
          onStartResearch={onStartResearch}
          explorations={explorations}
          explorationsPending={explorationsPending}
        />
      ) : onTray ? (
        <InboxTray
          threads={discussions}
          explorations={explorations}
          pending={explorationsPending || discussionsPending}
          onRetry={onRetryExploration}
          onAbandon={onAbandonExploration}
          onKeepSeed={onKeepSeed}
          onKeepMaterial={onKeepMaterial}
          onDismiss={onDismissCandidate}
          onKeepCard={onKeepCard}
          onDismissCard={onDismissCard}
          onVerify={onVerifyCard}
          onDiscuss={(topic) => {
            onDiscussQuestion({ topic, role_id: null });
            onTabChange("discussion");
          }}
        />
      ) : (
        <>
          {history != null && (
            <div className="loop__history">
              <span className="muted small">
                正在看历史轮次 {history.roundId}（只读，不能在这里答题或写入）
              </span>
              <button type="button" className="btn btn--ghost btn--sm" onClick={history.onBack}>
                回到最新一轮
              </button>
            </div>
          )}
          {round === null ? (
            <div className="loop__scroll">
              <div className="loop__head">
                <h2 className="loop__title">还没有开过一轮</h2>
              </div>
              <p className="muted small">
                把这一轮的想法写在下面。它会先复述给你看，你确认了才请席位发言，最后才可能写进文档。
              </p>
            </div>
          ) : (
            <>
              {chip !== null && (
                <div className="loop__chiprow">
                  <button
                    type="button"
                    className="loop__chip"
                    onClick={() => drafts.setCollapsed(false)}
                  >
                    {chip.label} · {chip.detail} ↗
                  </button>
                </div>
              )}
              <LoopPane
                round={round}
                pending={pending}
                limits={schema?.limits ?? null}
                ordinal={roundOrdinal(rounds, round.round_id)}
                elapsed={roundElapsed(rounds, round.round_id, Date.now())}
                historyNav={historyNav}
                historical={history !== null || mode === null}
                pendingWebRequest={pendingWebRequest}
                onRetry={onRetry}
                onAbandon={onAbandon}
                onSkipSeat={onSkipSeat}
                onOpenQuestionnaire={() => drafts.setCollapsed(false)}
                onOpenDraft={() => drafts.setCollapsed(false)}
                onDecideWebRequest={onDecideWebRequest}
                onRecoverWebRequest={onRecoverWebRequest}
              />
            </>
          )}

          {round !== null && drawerKind === "opening" && (
            <div className="loop__pop" role="dialog" aria-label="复述卡">
              <OpeningCard
                round={round}
                doc={doc}
                edits={drafts.edits}
                seats={drafts.seats}
                webQueries={drafts.webQueries}
                approvedRequestIds={approvedRequestIds}
                allowWeb={allowWeb}
                limit={schema?.limits.max_web_executions ?? null}
                pending={pending === "opening"}
                historyNav={historyNav}
                onChangeClaim={drafts.changeClaim}
                onToggleSeat={drafts.toggleSeat}
                onToggleWebQuery={drafts.toggleWebQuery}
                onToggleWebRequest={onToggleWebRequest}
                onToggleAllowWeb={onToggleAllowWeb}
                onSubmit={onSubmitOpening}
                onCollapse={() => drafts.setCollapsed(true)}
              />
            </div>
          )}

          {round !== null && drawerKind === "questionnaire" && (
            <div className="loop__pop" role="dialog" aria-label="问卷">
              <QuestionnaireDrawer
                round={round}
                doc={doc}
                drafts={drafts.answers}
                pending={pending === "answers"}
                onChange={drafts.changeAnswer}
                onSubmit={onSubmitAnswers}
                onSkipAll={onSkipAll}
                onDiscuss={onDiscussQuestion}
                onOpenTarget={onOpenTarget}
                onCollapse={() => drafts.setCollapsed(true)}
              />
            </div>
          )}

          {round !== null && drawerKind === "draft" && (
            <div className="loop__pop" role="dialog" aria-label="草案卡">
              <DraftDrawer
                round={round}
                doc={doc}
                accepted={history === null ? drafts.accepted : []}
                memoryAccepted={history === null ? drafts.memoryAccepted : []}
                pending={pending === "confirm"}
                selectionMatches={drafts.selectionMatches && canConfirmDraft(round, drafts.selection)}
                readOnly={history !== null}
                onToggle={drafts.toggleAccepted}
                onToggleMemory={drafts.toggleMemory}
                onSetAccepted={drafts.setAccepted}
                onSubmit={onConfirm}
                onDig={onDig}
                onSimInquiry={onSimInquiry}
                onSimReview={onSimReview}
                onOpenTarget={onOpenTarget}
                onCollapse={() => drafts.setCollapsed(true)}
              />
            </div>
          )}

          {simOpen && round !== null && (
            <SimInquiryDrawer
              key={`${round.sim_inquiry?.runs ?? 0}:${(round.sim_inquiry?.chosen ?? []).join(",")}`}
              inquiry={round.sim_inquiry ?? null}
              pending={simPending}
              initialChosen={round.sim_inquiry?.chosen ?? []}
              onKeep={onKeepSim}
              onClose={onCloseSim}
            />
          )}

          {reviewOpen && round !== null && (
            <SimReviewDrawer
              key={`review:${round.round_id}`}
              review={round.sim_review ?? null}
              personas={schema?.personas ?? []}
              document={doc}
              limits={reviewLimits(schema?.limits)}
              pending={reviewPending}
              onRun={onRunReview}
              onClose={onCloseReview}
            />
          )}

          {onRound && (
            <LoopComposer
              projectId={composerProjectId}
              composerMode={composerMode}
              pending={composerPending}
              drawerOpen={drawerOpen}
              value={composerValue}
              onChange={composerOnChange}
              onStartRound={composerOnStartRound}
              pendingSeedCount={composerPendingSeedCount}
              materials={composerMaterials}
              onMaterialUploaded={composerOnMaterialUploaded}
              onMaterialRemove={composerOnMaterialRemove}
              libraryCount={composerLibraryCount}
              onOpenLibrary={composerOnOpenLibrary}
              onOpenDrawer={() => drafts.setCollapsed(false)}
            />
          )}
        </>
      )}
    </div>
  );
}
