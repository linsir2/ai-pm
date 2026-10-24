/* 闭环区：本轮讨论流。按时间摆出这一轮发生了什么——
   你说了什么、它抽出了什么、每个席位交回了什么、最后整合成了什么。
   **只展示结构化产出与依据，不展示模型的思维链。** */

import type { ReactNode } from "react";

import { answerBatchSummary } from "../api/payload";
import type { LimitsView, RoundView } from "../api/types";
import { dropsFor, toolDrops, unplacedDrops } from "../document/dropped";
import { canSkipSeat } from "../document/status";
import { claimLabelText, compactTarget } from "../labels";
import { labelCounts, rewritesOf } from "../state/progress";
import { retrievalForPacket, retrievalForStage } from "../state/retrieval";
import { limitsOf } from "../state/schema";
import { CallTrace } from "./CallTrace";
import { EvidenceChip } from "./EvidenceChip";
import { RoundMaterials } from "./RoundMaterials";
import { RoundRetrieval } from "./RoundRetrieval";
import { RoundStatus } from "./RoundStatus";
import { SeatPacketCard } from "./SeatPacketCard";

export function LoopPane({
  round,
  pending,
  limits = null,
  ordinal = null,
  elapsed = null,
  historyNav,
  onRetry,
  onAbandon,
  onSkipSeat,
  onOpenQuestionnaire,
  onOpenDraft,
}: {
  round: RoundView;
  pending: string | null;
  /** `/schema` 里的上限；拿不到就回落（`06-前端.md` §5 记的重复因此退休）。 */
  limits?: LimitsView | null;
  /** 第几轮（展示序号，来自 rounds 列表）；算不出就是 null，标题回落成"本轮"。 */
  ordinal?: number | null;
  /** 这一轮跑了多久（LoopColumn 用 rounds 列表算好传进来；null 就不显示）。 */
  elapsed?: string | null;
  /** 头部右端的跨轮入口（"这个项目走过 N 轮"）；不传就不出。 */
  historyNav?: ReactNode;
  onRetry: () => void;
  onAbandon: () => void;
  /** 跳过一个席位（LoopColumn 从 App 接过来；卡片只负责点，判能不能点在下面）。 */
  onSkipSeat: (seatId: string) => void;
  onOpenQuestionnaire: () => void;
  onOpenDraft: () => void;
}) {
  const maxSeatRounds = limits?.max_seat_rounds ?? limitsOf().max_seat_rounds;
  const running = pending !== null;
  const counts = labelCounts(round);
  const rewriteCount = rewritesOf(round.draft);
  const integrated = round.questions.length > 0 || round.draft !== null;
  // 挂不到任何席位包上的留痕（地址写歪、或归因对不上号）：闭环区末尾单独一行。
  // 工具那几笔（检索失败 / 没带进去）不是席位的事，单独一段说（`P17b` §2.4）。
  // 自动检索进来的材料（`material_auto`，`02 §11.0.91`）也不是"丢掉的"——它们读到了，
  // 只是用户没勾；落进这个折叠会被那句"去处写错的"说反话，而且每轮都会出现。
  const unplaced = unplacedDrops(round.dropped, round.seat_packets).filter(
    (item) => item.source !== "tool" && item.source !== "material_auto",
  );
  const toolNotes = toolDrops(round.dropped);
  const dupItems = round.list_items.length - new Set(round.list_items).size;
  // 整合节点：两个动作各自只在"现在真能做"时出现（等答 / 等确认），做完不留死按钮。
  const actionable =
    (round.waiting_on === "questionnaire" && round.questions.length > 0) ||
    (round.waiting_on === "draft_card" && round.draft !== null);
  const seedRetrieval = retrievalForStage(round, "seed", 0);
  const integrateRetrieval = retrievalForStage(
    round,
    "integrate",
    round.integration_seq ?? 0,
  );

  return (
    <div className="loop__round">
      <header className="loop__round-head">
        <div className="loop__headrow">
          <h2 className="loop__round-title">
            {ordinal === null ? "本轮" : `Round ${String(ordinal).padStart(2, "0")}`}
          </h2>
          <span className="topbar__spacer" />
          {historyNav}
        </div>
        <RoundStatus
          round={round}
          pending={pending}
          elapsed={elapsed}
          onRetry={onRetry}
          onAbandon={onAbandon}
        />
      </header>

      <div className="loop__scroll">
        <RoundMaterials
          materials={round.materials}
          pinnedMaterials={round.pinned_materials ?? []}
          hasRetrievals={(round.retrievals ?? []).length > 0}
        />

        {/* 这一轮每一次外部调用（模型 + 工具）的时间序——只读，历史轮也显示。 */}
        <CallTrace roundId={round.round_id} />

        <ol className="timeline">
          <Node actor="你" title="本轮输入" meta="本轮">
            <blockquote className="quote">{round.user_input}</blockquote>
          </Node>

          <Node
            actor="Analyzer"
            title={
              round.claims.length > 0
                ? `已切出 ${round.claims.length} 条主张`
                : running
                  ? "主张抽取中…"
                  : "主张抽取"
            }
            meta={null}
          >
            {round.claims.length === 0 ? (
              <p className="muted small">还没抽主张。</p>
            ) : (
              <details className="q__whybox claims-fold">
                <summary className="muted small">
                  {round.claims.length} 条 · {counts.length} 种标签
                </summary>
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
                        <EvidenceChip eids={[claim.eid]} />
                        <span className="muted small">{compactTarget(claim.target_label)}</span>
                      </div>
                      <p className="claim__text">{claim.text}</p>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </Node>

          {seedRetrieval !== null && (
            <Node actor="检索" title="开局取料" meta={null}>
              <RoundRetrieval trace={seedRetrieval} />
            </Node>
          )}

          {round.seats.length > 0 && (
            <Node
              actor="席位轮"
              title={`请了 ${round.seats.length} 个席位`}
              meta={
                `第 ${Math.min(round.seat_round_count, maxSeatRounds)} / ${maxSeatRounds} 轮` +
                (round.active_seats.length < round.seats.length
                  ? ` · 只请 ${round.active_seats.length} 个（缩编）`
                  : "")
              }
            >
              {round.seat_packets.map((packet, order) => (
                <SeatPacketCard
                  packet={packet}
                  order={order + 1}
                  running={running}
                  drops={dropsFor(packet, round.dropped)}
                  skippable={canSkipSeat(packet, running, round.active_seats)}
                  onSkip={onSkipSeat}
                  retrieval={retrievalForPacket(round, packet)}
                  key={`${packet.seat_id}-${packet.pass_index}`}
                />
              ))}
              {unplaced.length > 0 && (
                <details className="q__whybox drops-fold">
                  <summary className="muted small">留痕 {unplaced.length} 条</summary>
                  <div className="packet__drops">
                    <p className="packet__counts packet__counts--drops">
                      另有 {unplaced.length} 条留痕没挂到某个席位包上
                      （去处写错的、同一件事问了两遍的、更早那趟的、或地址对不上号的）
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
                </details>
              )}
            </Node>
          )}

          {round.answer_sets.length > 0 && (
            <Node actor="你" title={`答过 ${round.answer_sets.length} 批`} meta={null}>
              <div className="answers">
                {round.answer_sets.map((batch, index) => (
                  <div className="answer-batch" key={batch.form_id}>
                    <p className="answer-batch__head muted small">
                      第 {index + 1} 批 · {answerBatchSummary(batch)}
                    </p>
                    <ul className="answer-batch__list">
                      {/* 答案串是后端 `answer_text()` 渲染好的「题目 → 选项；备注」整串，原样展示 */}
                      {batch.answers.map((answer, i) => (
                        <li className="answer" key={`${batch.form_id}-${i}`}>
                          {answer}
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </Node>
          )}

          {integrateRetrieval !== null && (
            <Node actor="检索" title="整合前取缺口证据" meta={null}>
              <RoundRetrieval trace={integrateRetrieval} />
            </Node>
          )}

          {integrated && (
            <Node
              actor="整合"
              title={`出了 ${[
                round.questions.length > 0 ? `${round.questions.length} 题` : null,
                round.draft !== null ? `${round.draft.proposals.length} 条候选` : null,
              ]
                .filter((part) => part !== null)
                .join(" · ")}`}
              meta={
                round.draft !== null && round.draft.proposals.length > 0
                  ? `按来源分 ${round.draft.groups.length} 组` +
                    (rewriteCount > 0 ? ` · ${rewriteCount} 条改写或删除` : "")
                  : null
              }
            >
              {actionable && (
                <div className="row">
                  {round.waiting_on === "questionnaire" && round.questions.length > 0 && (
                    <button
                      type="button"
                      className="btn--link"
                      onClick={onOpenQuestionnaire}
                      title="打开问卷"
                    >
                      查看问卷（{round.questions.length} 题）
                    </button>
                  )}
                  {round.waiting_on === "draft_card" && round.draft !== null && (
                    <button
                      type="button"
                      className="btn--link"
                      onClick={onOpenDraft}
                      title="打开草案卡"
                    >
                      查看草案（{round.draft.proposals.length} 条）
                    </button>
                  )}
                </div>
              )}
            </Node>
          )}
        </ol>

        {round.list_items.length > 0 && (
          <div className="loop__group">
            <details className="q__whybox">
              <summary className="muted small">
                先记成清单 {round.list_items.length} 条
                {dupItems > 0 && `（其中 ${dupItems} 条重复）`}
              </summary>
              <ul className="entries">
                {/* key 带序号：清单项文本会重复（真跑 39 条里 9 条重复），拿文本当 key 会撞（P29 F1） */}
                {round.list_items.map((item, index) => (
                  <li className="entry" key={`${item}-${index}`}>
                    <span className="entry__text muted small">{item}</span>
                  </li>
                ))}
              </ul>
            </details>
          </div>
        )}

        {toolNotes.length > 0 && (
          <div className="loop__group">
            <details className="q__whybox">
              <summary className="muted small">工具 {toolNotes.length} 条</summary>
              <ul className="drops">
                {toolNotes.map((item) => (
                  <li className="drop" key={`${item.handle}-${item.reason}`}>
                    <span className="mono small">{item.handle}</span>
                    <span className="drop__reason">{item.reason}</span>
                  </li>
                ))}
              </ul>
            </details>
          </div>
        )}
      </div>
    </div>
  );
}

function Node({
  actor,
  title,
  meta,
  children,
}: {
  actor: string;
  title: string;
  meta: string | null;
  children: ReactNode;
}) {
  return (
    <li className="node">
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
