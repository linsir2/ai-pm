/* 闭环栏（右栏）：上半是「本轮 / 灵感箱 / 讨论」三个页签。
   本轮讨论流里，到了要你决定的时候，问卷/草案从右侧滑出盖住这一栏。
   复述卡是特例——它不是抽屉，它就是这一轮的开场，所以占满整栏、且不显示页签
   （显示会把复述卡藏到「过目台」后面）。 */

import type { AwaitingMode } from "../api/payload";
import type {
  CandidateView,
  DocumentView,
  ExplorationView,
  RoundHeadView,
  RoundView,
  SchemaView,
  ThreadView,
} from "../api/types";
import type { DiscussionAnchor } from "../state/discussions";
import { discussionTurnLimit, roleOptions } from "../state/discussions";
import { inboxCount } from "../state/inbox";
import type { RoundDrafts } from "../state/useRoundDrafts";
import { DiscussionTray } from "./DiscussionTray";
import { DraftDrawer } from "./DraftDrawer";
import { InboxTray } from "./InboxTray";
import { LoopPane } from "./LoopPane";
import { OpeningCard } from "./OpeningCard";
import { QuestionnaireDrawer } from "./QuestionnaireDrawer";
import { SimInquiryDrawer } from "./SimInquiryDrawer";
import { SimReviewDrawer } from "./SimReviewDrawer";
import { reviewLimits } from "../state/simReview";
import { RoundHistory } from "./RoundHistory";

export type LoopTab = "round" | "tray" | "discussion";

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
  discussions = [],
  discussionsPending = false,
  discussionAnchor = null,
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
  onSayToThread,
  onAssembleCards,
  onKeepDiscussionCard,
  onDismissDiscussionCard,
  onCloseDiscussion,
  onDiscussQuestion,
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
  onSubmitOpening: () => void;
  onSubmitAnswers: () => void;
  onSkipAll: () => void;
  onConfirm: () => void;
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
  /** 切过去只读看某一轮（复用已有的 `viewRound`）。 */
  onViewRound: (roundId: string) => void;
  /** 发起调研（整份文档；条目入口在文档的条目工具行上）。 */
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
  /** 讨论 tab 的数据源（M9）：这个项目开过的线程（最新在前）。 */
  discussions?: ThreadView[];
  discussionsPending?: boolean;
  /** 问卷里点「聊聊」带过来的题面与问的人（null = 自由开聊）。 */
  discussionAnchor?: DiscussionAnchor | null;
  onStartDiscussion: (anchor: { topic: string; role_id: string }, runInquiry: boolean) => void;
  onSayToThread: (threadId: string, text: string) => void;
  onAssembleCards: (threadId: string) => void;
  onKeepDiscussionCard: (threadId: string, cardId: string, as: "seed" | "material") => void;
  onDismissDiscussionCard: (threadId: string, cardId: string) => void;
  onCloseDiscussion: (threadId: string) => void;
  /** 问卷题行上的「聊聊」：只把锚点交上去（不直接发请求）。 */
  onDiscussQuestion: (anchor: DiscussionAnchor) => void;
}) {
  const showTabs = mode !== "opening";
  const onTray = tab === "tray" && showTabs;
  const onDiscussion = tab === "discussion" && showTabs;
  const onRound = !onTray && !onDiscussion;
  // 灵感箱的未处理数：新卡 + 待过目候选（0 条不渲染徽标）
  const inbox = inboxCount(discussions, explorations);
  const drawerOpen =
    history == null &&
    !drafts.collapsed && (mode === "questionnaire" || mode === "draft") && round !== null;

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
        </div>
      )}

      {onDiscussion ? (
        <DiscussionTray
          threads={discussions}
          roles={roleOptions(schema)}
          turnLimit={discussionTurnLimit(schema?.limits)}
          anchor={discussionAnchor}
          pending={discussionsPending}
          onStart={onStartDiscussion}
          onSay={onSayToThread}
          onCards={onAssembleCards}
          onKeep={onKeepDiscussionCard}
          onDismiss={onDismissDiscussionCard}
          onVerify={onVerifyCard}
          onClose={onCloseDiscussion}
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
          onStartDocument={onStartResearch}
        />
      ) : (
        <>
          <RoundHistory
            rounds={rounds}
            currentRoundId={round?.round_id ?? null}
            onView={onViewRound}
          />
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
          {mode === "opening" && round !== null ? (
            <OpeningCard
              round={round}
              doc={doc}
              edits={drafts.edits}
              seats={drafts.seats}
              webQueries={drafts.webQueries}
              pending={pending === "opening"}
              onChangeClaim={drafts.changeClaim}
              onToggleSeat={drafts.toggleSeat}
              onToggleWebQuery={drafts.toggleWebQuery}
              onSubmit={onSubmitOpening}
            />
          ) : round === null ? (
            <div className="loop__scroll">
              <div className="loop__head">
                <h2 className="loop__title">还没有开过一轮</h2>
              </div>
              <p className="muted small">
                把这一轮的想法写在下面。它会先复述给你看，你确认了才请席位发言，最后才可能写进文档。
              </p>
            </div>
          ) : (
            <LoopPane
              round={round}
              pending={pending}
              limits={schema?.limits ?? null}
              onRetry={onRetry}
              onAbandon={onAbandon}
              onOpenQuestionnaire={() => drafts.setCollapsed(false)}
              onOpenDraft={() => drafts.setCollapsed(false)}
            />
          )}

          {drawerOpen && mode === "questionnaire" && round !== null && (
            <QuestionnaireDrawer
              round={round}
              doc={doc}
              drafts={drafts.answers}
              pending={pending === "answers"}
              onChange={drafts.changeAnswer}
              onSubmit={onSubmitAnswers}
              onSkipAll={onSkipAll}
              onDiscuss={onDiscussQuestion}
              onCollapse={() => drafts.setCollapsed(true)}
            />
          )}

          {drawerOpen && mode === "draft" && round !== null && (
            <DraftDrawer
              round={round}
              doc={doc}
              accepted={drafts.accepted}
              memoryAccepted={drafts.memoryAccepted}
              pending={pending === "confirm"}
              onToggle={drafts.toggleAccepted}
              onToggleMemory={drafts.toggleMemory}
              onSetAccepted={drafts.setAccepted}
              onSubmit={onConfirm}
              onDig={onDig}
              onSimInquiry={onSimInquiry}
              onSimReview={onSimReview}
              onCollapse={() => drafts.setCollapsed(true)}
            />
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
        </>
      )}
    </div>
  );
}
