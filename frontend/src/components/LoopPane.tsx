/* 闭环区：本轮讨论流。按时间摆出这一轮发生了什么——
   你说了什么、它抽出了什么、每个席位交回了什么、整合成了什么。
   **只展示结构化产出与依据，不展示模型的思维链。** */

import type { ReactNode } from "react";

import { integrationSummary } from "../api/payload";
import type { LimitsView, RoundView } from "../api/types";
import { dropsFor, toolDrops, unplacedDrops } from "../document/dropped";
import { claimLabelText, compactTarget } from "../labels";
import { labelCounts, rewritesOf } from "../state/progress";
import { limitsOf } from "../state/schema";
import { ProgressSteps } from "./ProgressSteps";
import { RoundMaterials } from "./RoundMaterials";
import { RoundStatus } from "./RoundStatus";
import { SeatPacketCard } from "./SeatPacketCard";

export function LoopPane({
  round,
  pending,
  limits = null,
  ordinal = null,
  onRetry,
  onAbandon,
  onOpenQuestionnaire,
  onOpenDraft,
}: {
  round: RoundView;
  pending: string | null;
  /** `/schema` 里的上限；拿不到就回落（`06-前端.md` §5 记的重复因此退休）。 */
  limits?: LimitsView | null;
  /** 第几轮（展示序号，来自 rounds 列表）；算不出就是 null，标题回落成"本轮"。 */
  ordinal?: number | null;
  onRetry: () => void;
  onAbandon: () => void;
  onOpenQuestionnaire: () => void;
  onOpenDraft: () => void;
}) {
  const maxSeatRounds = limits?.max_seat_rounds ?? limitsOf().max_seat_rounds;
  const running = pending !== null;
  const counts = labelCounts(round);
  const integrated = round.questions.length > 0 || round.draft !== null;
  // 挂不到任何席位包上的留痕（地址写歪、或归因对不上号）：闭环区末尾单独一行。
  // 工具那几笔（检索失败 / 没带进去）不是席位的事，单独一段说（`P17b` §2.4）。
  const unplaced = unplacedDrops(round.dropped, round.seat_packets).filter(
    (item) => item.source !== "tool",
  );
  const toolNotes = toolDrops(round.dropped);

  return (
    <div className="loop__round">
      <header className="loop__round-head">
        <h2 className="loop__round-title">
          {ordinal === null ? "本轮" : `Round ${String(ordinal).padStart(2, "0")}`}
        </h2>
        <span className="loop__hint">只展示结构化产出</span>
      </header>

      <RoundStatus round={round} pending={pending} onRetry={onRetry} onAbandon={onAbandon} />

      <div className="loop__split">
        <ProgressSteps round={round} limits={limits} />

        <div className="loop__scroll">
          <RoundMaterials materials={round.materials} />

          <ol className="timeline">
            <Node index={1} actor="你" title="本轮输入" meta="本轮">
              <blockquote className="quote">{round.user_input}</blockquote>
            </Node>

            <Node
              index={2}
              actor="Analyzer"
              title="主张抽取"
              meta={
                round.claims.length === 0
                  ? null
                  : `${round.claims.length} 条 · ${counts.length} 种标签`
              }
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
                      <li
                        className="claim claim--compact"
                        key={claim.claim_id}
                        data-eid={claim.eid}
                      >
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
                  `第 ${Math.min(round.seat_round_count, maxSeatRounds)} / ${maxSeatRounds} 轮` +
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
                      {/* key 带序号：同一条留痕（handle + reason 都一样）会出现多次，拿它俩拼 key 会撞（P29 F2） */}
                      {unplaced.map((item, index) => (
                        <li className="drop" key={`${item.handle}-${item.reason}-${index}`}>
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
                        round.waiting_on === "draft_card"
                          ? "打开草案卡"
                          : "这一轮的草案已经写完了"
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
                {/* key 带序号：清单项文本会重复（真跑 39 条里 9 条重复），拿文本当 key 会撞（P29 F1） */}
                {round.list_items.map((item, index) => (
                  <li className="entry" key={`${item}-${index}`}>
                    <span className="entry__text muted small">{item}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {toolNotes.length > 0 && (
            <div className="loop__group">
              <p className="loop__label">工具这一趟（联网检索）</p>
              <ul className="drops">
                {toolNotes.map((item) => (
                  <li className="drop" key={`${item.handle}-${item.reason}`}>
                    <span className="mono small">{item.handle}</span>
                    <span className="drop__reason">{item.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
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
