/* 闭环栏（右栏）：本轮讨论流；到了要你决定的时候，问卷/草案从右侧滑出盖住这一栏。
   复述卡是特例——它不是抽屉，它就是这一轮的开场，所以占满整栏。 */

import type { AwaitingMode } from "../api/payload";
import type { DocumentView, RoundView } from "../api/types";
import type { RoundDrafts } from "../state/useRoundDrafts";
import { DraftDrawer } from "./DraftDrawer";
import { LoopPane } from "./LoopPane";
import { OpeningCard } from "./OpeningCard";
import { QuestionnaireDrawer } from "./QuestionnaireDrawer";

export function LoopColumn({
  round,
  doc,
  mode,
  pending,
  drafts,
  history = null,
  onSubmitOpening,
  onSubmitAnswers,
  onSkipAll,
  onConfirm,
  onRetry,
  onAbandon,
}: {
  round: RoundView | null;
  doc: DocumentView | null;
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
  onRetry: () => void;
  onAbandon: () => void;
}) {
  const drawerOpen =
    history == null &&
    !drafts.collapsed && (mode === "questionnaire" || mode === "draft") && round !== null;

  return (
    <div className="loop">
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
          pending={pending === "opening"}
          onChangeClaim={drafts.changeClaim}
          onToggleSeat={drafts.toggleSeat}
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
          pending={pending === "confirm"}
          onToggle={drafts.toggleAccepted}
          onSetAccepted={drafts.setAccepted}
          onSubmit={onConfirm}
          onCollapse={() => drafts.setCollapsed(true)}
        />
      )}
    </div>
  );
}
