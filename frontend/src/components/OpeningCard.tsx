/* 复述卡：它把你的话复述回来 —— 标签对不对、去处对不对、这一轮请谁。
   只改标签与去处（改文本归文档，见 01-规格 §5 的硬约束）。 */

import type { ClaimEdit } from "../api/payload";
import { seatNominationSummary, selectableLabels } from "../api/payload";
import type { DocumentView, RoundView } from "../api/types";
import { claimLabelText } from "../labels";
import { EvidenceChip } from "./EvidenceChip";

export function OpeningCard({
  round,
  doc,
  edits,
  seats,
  pending,
  onChangeClaim,
  onToggleSeat,
  onSubmit,
}: {
  round: RoundView;
  doc: DocumentView | null;
  edits: Record<string, ClaimEdit>;
  seats: string[];
  pending: boolean;
  onChangeClaim: (claimId: string, edit: ClaimEdit) => void;
  onToggleSeat: (seatId: string) => void;
  onSubmit: () => void;
}) {
  const sections = doc?.sections ?? [];
  const touched = touchedTargets(round, edits);
  const alwaysOn = round.nominated_seats.filter((seat) => seat.always_on).length;
  const triggered = round.nominated_seats.filter((seat) => seat.matched_keywords.length > 0);
  const summary = seatNominationSummary(round);

  return (
    <section className="pane-card" aria-label="复述卡">
      <header className="drawer__head">
        <h2 className="drawer__title">复述 · 我读到的 {round.claims.length} 件事</h2>
      </header>

      <div className="drawer__body">
        <p className="q__why">
          下面是它从你这段话里切出来的主张，每条带一个标签（决定怎么处理）和一个去处（决定写进哪）。
          有偏差就在这儿改；改完才开跑席位。
        </p>

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
                        sections.map((section) => (
                          <option key={section.section_id} value={section.section_id}>
                            {section.section_id} {section.title}
                          </option>
                        ))
                      )}
                    </select>
                  </label>
                  <EvidenceChip eids={[claim.eid]} />
                </div>
                <p className="claim__raw">原话「{claim.raw_span}」</p>
                {claim.assumption_note !== null && (
                  <p className="claim__raw">猜测升格成的假设：{claim.assumption_note}</p>
                )}
                {claim.label === "guess" && (
                  <p className="claim__note">
                    这条是猜测：它带了"失败后果 + 怎么验证"的假设，标签改不动（改假设归文档）。
                  </p>
                )}
              </li>
            );
          })}
        </ul>

        <fieldset className="seats">
          <legend className="loop__label">这一轮请哪些席位</legend>
          <p className="seat__note">
            {summary.source === "agent" ? (
              <>
                这 {summary.nominated} 个是<strong>分析席位读完你这段话之后挑的</strong>
                （每条都写了理由）；不合适的就取消勾选。
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
                <span>
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
                  <span className="seat__focus">它管什么：{seat.focus}</span>
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
                    disabled={seats.includes(seat.seat_id)}
                  >
                    {seats.includes(seat.seat_id) ? "已加上" : "+ 加上"}
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
