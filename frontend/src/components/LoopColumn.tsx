/* 闭环栏（右栏）：本轮讨论流；到了要你决定的时候，问卷/草案从右侧滑出盖住这一栏。
   复述卡是特例——它不是抽屉，它就是这一轮的开场，所以占满整栏。 */

import type { AwaitingMode } from "../api/payload";
import type { DocumentView, RoundHeadView, RoundView, SchemaView } from "../api/types";
import type { RoundDrafts } from "../state/useRoundDrafts";
import { DraftDrawer } from "./DraftDrawer";
import { LoopPane } from "./LoopPane";
import { OpeningCard } from "./OpeningCard";
import { QuestionnaireDrawer } from "./QuestionnaireDrawer";
import { RoundHistory } from "./RoundHistory";

export function LoopColumn({
  round,
  doc,
  schema,
  rounds = [],
  mode,
  pending,
  drafts,
  history = null,
  onSubmitOpening,
  onSubmitAnswers,
  onSkipAll,
  onConfirm,
  onDig,
  onRetry,
  onAbandon,
  onViewRound,
}: {
  round: RoundView | null;
  doc: DocumentView | null;
  /** `GET /schema` 的投影：只用来取上限常量（拿不到就由下游回落）。 */
  schema: SchemaView | null;
  /** 这个项目走过的轮次（最新在前）：跨轮的入口（F4）。 */
  rounds?: RoundHeadView[];
  /** null = 只看不算数（正在看历史轮：不出开场卡、不出抽屉）。 */
  mode: AwaitingMode | null;
  pending: string | null;
  drafts: RoundDrafts;
  /** 正在看历史轮（只读）时给出的横幅；null = 正常看当前这一轮。 */
  history?: { roundId: string; onBack: () => void } | null;
  onSubmitOpening: () => void;
  onSubmitAnswers: () => void;
  onSkipAll: () => void;
  onConfirm: () => void;
  onDig: () => void;
  onRetry: () => void;
  onAbandon: () => void;
  /** 切过去只读看某一轮（复用已有的 `viewRound`）。 */
  onViewRound: (roundId: string) => void;
}) {
  const drawerOpen =
    history == null &&
    !drafts.collapsed && (mode === "questionnaire" || mode === "draft") && round !== null;

  return (
    <div className="loop">
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
          onCollapse={() => drafts.setCollapsed(true)}
        />
      )}
    </div>
  );
}
