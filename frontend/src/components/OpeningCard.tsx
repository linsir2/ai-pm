/* 复述卡：它把你的话复述回来 —— 标签对不对、去处对不对、这一轮请谁。
   只改标签与去处（改文本归文档，见 01-规格 §5 的硬约束）。 */

import type { ReactNode } from "react";

import type { ClaimEdit } from "../api/payload";
import { seatNominationSummary, selectableLabels, targetOptions } from "../api/payload";
import type { DocumentView, RoundView } from "../api/types";
import { claimLabelText } from "../labels";
import { EvidenceChip } from "./EvidenceChip";
import { RoundMaterials } from "./RoundMaterials";

export function OpeningCard({
  round,
  doc,
  edits,
  seats,
  webQueries,
  pending,
  historyNav,
  onChangeClaim,
  onToggleSeat,
  onToggleWebQuery,
  onSubmit,
}: {
  round: RoundView;
  doc: DocumentView | null;
  edits: Record<string, ClaimEdit>;
  seats: string[];
  webQueries: string[];
  pending: boolean;
  /** 头部右端的跨轮入口（"这个项目走过 N 轮"）；不传就不出。 */
  historyNav?: ReactNode;
  onChangeClaim: (claimId: string, edit: ClaimEdit) => void;
  onToggleSeat: (seatId: string) => void;
  onToggleWebQuery: (query: string) => void;
  onSubmit: () => void;
}) {
  const touched = touchedTargets(round, edits);
  const alwaysOn = round.nominated_seats.filter((seat) => seat.always_on).length;
  const triggered = round.nominated_seats.filter((seat) => seat.matched_keywords.length > 0);
  const summary = seatNominationSummary(round);

  return (
    <section className="pane-card" aria-label="复述卡">
      <header className="drawer__head">
        <h2 className="drawer__title">复述 · 我读到的 {round.claims.length} 件事</h2>
        <span className="topbar__spacer" />
        {historyNav}
      </header>

      <div className="drawer__body">
        <p className="q__why">
          核对标签与去处，改完才开跑席位。
        </p>

        {round.analysis_basis ? (
          <p className="q__why q__why--basis">
            它这一趟读了：{round.analysis_basis}
          </p>
        ) : null}

        <RoundMaterials materials={round.materials} />

        <ul className="claims">
          {round.claims.map((claim) => {
            const edit = edits[claim.claim_id] ?? {
              label: claim.label,
              target_label: claim.target_label,
            };
            const labels = selectableLabels(claim.label);
            return (
              <li className="claim" key={claim.claim_id} data-eid={claim.eid}>
                <p className="claim__text">{claim.text}</p>
                <details className="q__whybox">
                  <summary className="muted small">原话</summary>
                  <p className="claim__raw">原话「{claim.raw_span}」</p>
                  {claim.assumption_note !== null && (
                    <p className="claim__raw">猜测升格成的假设：{claim.assumption_note}</p>
                  )}
                  {claim.label === "guess" && (
                    <p className="claim__note">
                      这条是猜测：它带了"失败后果 + 怎么验证"的假设，标签改不动（改假设归文档）。
                    </p>
                  )}
                </details>
                <div className="claim__controls">
                  <label className="claim__field">
                    <span className="sr-only">标签</span>
                    <select
                      className="select"
                      value={edit.label}
                      disabled={labels.length === 1}
                      onChange={(event) =>
                        onChangeClaim(claim.claim_id, { ...edit, label: event.target.value })
                      }
                    >
                      {labels.map((label) => (
                        <option key={label} value={label}>
                          {claimLabelText(label)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="claim__field">
                    <span className="sr-only">去处</span>
                    <select
                      className="select"
                      value={edit.label === "preference" ? "preference" : edit.target_label}
                      disabled={edit.label === "preference"}
                      onChange={(event) =>
                        onChangeClaim(claim.claim_id, { ...edit, target_label: event.target.value })
                      }
                    >
                      {edit.label === "preference" ? (
                        <option value="preference">偏好区</option>
                      ) : (
                        targetOptions(doc).map((option) => (
                          <option key={option.value} value={option.value}>
                            {option.text}
                          </option>
                        ))
                      )}
                    </select>
                  </label>
                  <EvidenceChip eids={[claim.eid]} />
                </div>
              </li>
            );
          })}
        </ul>

        <fieldset className="seats">
          <legend className="loop__label">这一轮请哪些席位</legend>
          <p className="seat__note">
            {summary.source === "agent" ? (
              <>
                分析席位按你这段话挑了 {summary.nominated} 个，每条有理由；不合适取消勾选。
                {summary.available > 0 && ` 目录里还有 ${summary.available} 个可以自己加。`}
              </>
            ) : (
              <>
                这一轮的名单是<strong>按目录关键词回落的</strong>（常驻 {alwaysOn} 个
                {triggered.length > 0
                  ? `，另加命中「${triggered.map((seat) => seat.matched_keywords[0]).join("」「")}」的`
                  : ""}
                ）——它没经过分析席位的判断，你多看两眼。
              </>
            )}
          </p>
          {round.nominated_seats.map((seat) => {
            const on = seats.includes(seat.seat_id);
            return (
              <label className={on ? "seat seat--on" : "seat"} key={seat.seat_id}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => onToggleSeat(seat.seat_id)}
                />
                <span title={`它管什么：${seat.focus}`}>
                  <span className="seat__title">
                    {seat.title}
                    <span className="seat__tag">
                      {seat.matched_keywords.length > 0
                        ? `命中「${seat.matched_keywords.join("」「")}」`
                        : seat.always_on
                          ? "常驻"
                          : "触发"}
                    </span>
                  </span>
                  {seat.why.trim() !== "" && <span className="seat__why">{seat.why}</span>}
                </span>
              </label>
            );
          })}

          {round.available_seats.length > 0 && (
            <details className="seats__more">
              <summary>目录里还有 {round.available_seats.length} 个席位，想加就加</summary>
              {round.available_seats.map((seat) => (
                <div className="seat seat--extra" key={seat.seat_id}>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => onToggleSeat(seat.seat_id)}
                  >
                    {seats.includes(seat.seat_id) ? "已加上 · 取消" : "+ 加上"}
                  </button>
                  <span>
                    <span className="seat__title">{seat.title}</span>
                    <span className="seat__focus">它管什么：{seat.focus}</span>
                  </span>
                </div>
              ))}
            </details>
          )}
        </fieldset>

        {round.web_queries.length > 0 && (
          <fieldset className="seats web-queries">
            <legend className="loop__label">联网检索</legend>
            {round.web_search_available ? (
              <>
                <p className="seat__note">
                  分析席位建议查 {round.web_queries.length} 条，默认不查；勾了才查。
                </p>
                {round.web_queries.map((item) => {
                  const on = webQueries.includes(item.query);
                  return (
                    <label className={on ? "seat seat--on" : "seat"} key={item.query}>
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() => onToggleWebQuery(item.query)}
                        aria-label={item.query}
                      />
                      <span>
                        <span className="seat__title">{item.query}</span>
                        {item.why.trim() !== "" && (
                          <span className="seat__why">为什么查它：{item.why}</span>
                        )}
                        <span className="seat__focus">
                          查回来的东西算<strong>外部材料</strong>：它只进"它提议的"那一组，默认不写入文档。
                        </span>
                      </span>
                    </label>
                  );
                })}
              </>
            ) : (
              <p className="seat__note">
                分析席位建议查 {round.web_queries.length} 条，但服务端没配联网工具（缺密钥），
                这一栏先帮不了你——想查就先把工具配好。
              </p>
            )}
          </fieldset>
        )}
      </div>

      <footer className="drawer__foot">
        <p className="input__hint">
          本轮将改动：{touched.length === 0 ? "（还没有主张）" : touched.join(" · ")}
        </p>
        <div className="row">
          <button
            type="button"
            className="btn btn--primary"
            disabled={pending || seats.length === 0}
            onClick={onSubmit}
          >
            {pending ? "席位在跑…" : `确认，开跑 ${seats.length} 个席位`}
          </button>
          {seats.length === 0 && <span className="muted small">至少要请一个席位</span>}
        </div>
      </footer>
    </section>
  );
}

/** 按修正后的去处汇总"这一轮会动到哪些章"（偏好区单独说）。 */
function touchedTargets(round: RoundView, edits: Record<string, ClaimEdit>): string[] {
  const targets = round.claims.map((claim) => {
    const edit = edits[claim.claim_id];
    if (edit === undefined) return claim.target_label;
    return edit.label === "preference" ? "preference" : edit.target_label;
  });
  const unique = Array.from(new Set(targets)).sort();
  return unique
    .sort((a, b) => orderOf(a) - orderOf(b))
    .map((target) => (target === "preference" ? "偏好区" : target));
}

/** s1 < s2 < … < 偏好区（偏好区不进正文，排在最后说）。 */
function orderOf(target: string): number {
  if (target === "preference") return Number.MAX_SAFE_INTEGER;
  const digits = Number.parseInt(target.replace(/^\D+/, ""), 10);
  return Number.isNaN(digits) ? Number.MAX_SAFE_INTEGER - 1 : digits;
}
