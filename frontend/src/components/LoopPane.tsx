/* 闭环区：本轮讨论流。按时间摆出这一轮发生了什么——
   你说了什么、它抽出了什么、每个席位交回了什么、整合成了什么。
   **只展示结构化产出与依据，不展示模型的思维链。** */

import type { ReactNode } from "react";

import { integrationSummary } from "../api/payload";
import type { RoundView } from "../api/types";
import { dropsFor, unplacedDrops } from "../document/dropped";
import { claimLabelText, compactTarget } from "../labels";
import { labelCounts, rewritesOf } from "../state/progress";
import { ProgressSteps } from "./ProgressSteps";
import { RoundStatus } from "./RoundStatus";
import { SeatPacketCard } from "./SeatPacketCard";

const MAX_SEAT_ROUNDS = 3;

export function LoopPane({
  round,
  pending,
  onRetry,
  onAbandon,
  onOpenQuestionnaire,
  onOpenDraft,
}: {
  round: RoundView;
  pending: string | null;
  onRetry: () => void;
  onAbandon: () => void;
  onOpenQuestionnaire: () => void;
  onOpenDraft: () => void;
}) {
  const running = pending !== null;
  const counts = labelCounts(round);
  const integrated = round.questions.length > 0 || round.draft !== null;
  // 挂不到任何席位包上的留痕（地址写歪、或归因对不上号）：闭环区末尾单独一行
  const unplaced = unplacedDrops(round.dropped, round.seat_packets);

  return (
    <div className="loop__scroll">
      <div className="loop__head">
        <h2 className="loop__title">本轮讨论</h2>
        <span className="loop__hint">只展示结构化产出</span>
      </div>

      <RoundStatus
        round={round}
        pending={pending}
        onRetry={onRetry}
        onAbandon={onAbandon}
      />
      <ProgressSteps round={round} />

      <ol className="timeline">
        <Node index={1} actor="你" title="本轮输入" meta="本轮">
          <blockquote className="quote">{round.user_input}</blockquote>
        </Node>

        <Node
          index={2}
          actor="Analyzer"
          title="主张抽取"
          meta={round.claims.length === 0 ? null : `${round.claims.length} 条 · ${counts.length} 种标签`}
        >
          {round.claims.length === 0 ? (
            <p className="muted small">还没抽主张。</p>
          ) : (
            <>
              <p className="label-row">
                {counts.map(([label, count]) => (
                  <span className={`tag tag--${label}`} key={label}>
                    {claimLabelText(label)} ×{count}
                  </span>
                ))}
              </p>
              <ul className="claims">
                {round.claims.map((claim) => (
                  <li className="claim claim--compact" key={claim.claim_id} data-eid={claim.eid}>
                    <div className="claim__head">
                      <span className="muted small mono">{claim.eid}</span>
                      <span className="muted small">{compactTarget(claim.target_label)}</span>
                    </div>
                    <p className="claim__text">{claim.text}</p>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Node>

        {round.seats.length > 0 && (
          <Node
            index={3}
            actor="席位轮"
            title={`${round.seats.length} 个席位`}
            meta={
              `第 ${Math.min(round.seat_round_count, MAX_SEAT_ROUNDS)} / ${MAX_SEAT_ROUNDS} 轮` +
              (round.active_seats.length < round.seats.length
                ? ` · 这一趟只请了 ${round.active_seats.length} 个（缩编：只请与本次答案相关的面）`
                : "")
            }
          >
            {round.seat_packets.map((packet, order) => (
              <SeatPacketCard
                packet={packet}
                order={order + 1}
                running={running}
                drops={dropsFor(packet, round.dropped)}
                key={`${packet.seat_id}-${packet.pass_index}`}
              />
            ))}
            {unplaced.length > 0 && (
              <div className="packet__drops">
                <p className="packet__counts packet__counts--drops">
                  另有 {unplaced.length} 条留痕没挂到某个席位包上
                  （去处写错的、更早那趟的、或地址对不上号的）
                </p>
                <ul className="drops">
                  {unplaced.map((item) => (
                    <li className="drop" key={`${item.handle}-${item.reason}`}>
                      <span className="mono small">{item.handle}</span>
                      <span className="drop__reason">{item.reason}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Node>
        )}

        {integrated && (
          <Node index={4} actor="整合" title="组装" meta={null}>
            <p className="muted small">{integrationSummary(round)}</p>
            {round.draft !== null && round.draft.proposals.length > 0 && (
              <p className="muted small">
                按来源分 {round.draft.groups.length} 组
                {rewritesOf(round.draft) > 0 && ` · ${rewritesOf(round.draft)} 条改写或删除`}
              </p>
            )}
            <div className="row">
              {round.questions.length > 0 && (
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={onOpenQuestionnaire}
                  disabled={round.waiting_on !== "questionnaire"}
                  title={
                    round.waiting_on === "questionnaire"
                      ? "打开问卷"
                      : "这一轮的问卷已经交过了"
                  }
                >
                  查看问卷（{round.questions.length} 题）
                </button>
              )}
              {round.draft !== null && (
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={onOpenDraft}
                  disabled={round.waiting_on !== "draft_card"}
                  title={
                    round.waiting_on === "draft_card" ? "打开草案卡" : "这一轮的草案已经写完了"
                  }
                >
                  查看草案（{round.draft.proposals.length} 条）
                </button>
              )}
            </div>
          </Node>
        )}
      </ol>

      {round.list_items.length > 0 && (
        <div className="loop__group">
          <p className="loop__label">先记成清单（超过一批 5 题的）</p>
          <ul className="entries">
            {round.list_items.map((item) => (
              <li className="entry" key={item}>
                <span className="entry__text muted small">{item}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function Node({
  index,
  actor,
  title,
  meta,
  children,
}: {
  index: number;
  actor: string;
  title: string;
  meta: string | null;
  children: ReactNode;
}) {
  return (
    <li className="node">
      <span className="node__index mono" aria-hidden="true">
        {String(index).padStart(2, "0")}
      </span>
      <div className="node__body">
        <div className="node__head">
          <span className="node__actor">{actor}</span>
          <span className="node__title">{title}</span>
          <span className="topbar__spacer" />
          {meta !== null && <span className="node__meta">{meta}</span>}
        </div>
        {children}
      </div>
    </li>
  );
}
