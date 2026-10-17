import type { SeatPacketView } from "../api/types";
import type { DroppedView } from "../api/types";
import { dropHeadline } from "../document/dropped";
import { opinionFormText } from "../labels";
import { EvidenceChip } from "./EvidenceChip";

/** 一个席位交回的信息包：几条判断、几条疑问、依据哪些编号。**没有思维链。** */
export function SeatPacketCard({
  packet,
  order,
  running,
  drops = [],
  skippable = false,
  onSkip,
}: {
  packet: SeatPacketView;
  order: number;
  running: boolean;
  /** 这个席位这一趟"没进卡片"的意见（原因由后端留痕，界面只摆出来）。 */
  drops?: DroppedView[];
  /** 能不能点"跳过"（LoopPane 用 `canSkipSeat` 算好传进来，卡片自己不判断）。 */
  skippable?: boolean;
  /** 点跳过（只在 `skippable` 为真时渲染按钮；不传就不出）。 */
  onSkip?: (seatId: string) => void;
}) {
  const completed = packet.status === "completed";
  const skipped = packet.status === "skipped";
  const pending = !completed && !skipped;
  const stateText = completed
    ? "已完成"
    : skipped
      ? "已跳过"
      : running
        ? "正在交回…"
        : "待启动";

  return (
    <article
      className={pending ? "packet packet--pending" : "packet"}
      aria-label={`席位 ${packet.title}`}
    >
      <div className="packet__head">
        <span className="packet__index">{String(order).padStart(2, "0")}</span>
        <h3 className="packet__title">{packet.title}</h3>
        <span className={pending && running ? "packet__state packet__state--running" : "packet__state"}>
          {stateText}
        </span>
        {skippable && onSkip !== undefined && (
          <button
            type="button"
            className="packet__skip"
            aria-label={`跳过席位 ${packet.title}`}
            onClick={() => onSkip(packet.seat_id)}
          >
            跳过
          </button>
        )}
      </div>

      {completed && (
        <>
          {/* 它是真的有话说，还是交了个空——这一行要说清楚，别只丢两个 0 出来 */}
          <p className="packet__basis">
            {packet.basis.trim() === "" ? "它没写自己读了什么。" : packet.basis}
          </p>

          {packet.opinions.length === 0 ? (
            <p className="packet__counts packet__counts--none">
              这个席位这一轮没有要补的（交回空意见，理由就是上面那段）
              <span className="muted"> · 第 {packet.pass_index} 轮</span>
            </p>
          ) : (
            <details className="packet__opinions">
              <summary className="packet__counts">
                {packet.judgment_count > 0 && <span>判断 {packet.judgment_count} 条</span>}
                {packet.question_count > 0 && <span>疑问 {packet.question_count} 个</span>}
                <span className="muted">第 {packet.pass_index} 轮</span>
              </summary>
              <ul className="opinions">
                {packet.opinions.map((opinion) => (
                  <li
                    className={`opinion opinion--${opinion.form}`}
                    key={opinion.local_id}
                    data-eid={opinion.eid ?? undefined}
                  >
                    <div className="opinion__head">
                      <span className={opinion.form === "question" ? "tag" : "tag tag--decision"}>
                        {opinionFormText(opinion.form)}
                      </span>
                      <span className="opinion__about">{opinion.about}</span>
                    </div>
                    <span className="opinion__claim">{opinion.claim}</span>
                    {opinion.because.trim() !== "" && (
                      <span className="opinion__because">理由：{opinion.because}</span>
                    )}
                    {opinion.basis_note !== null && (
                      <span className="opinion__because">{opinion.basis_note}</span>
                    )}
                    <div className="prop__foot">
                      <EvidenceChip eids={opinion.eids} />
                      {opinion.eid !== null && <EvidenceChip eids={[opinion.eid]} />}
                    </div>
                  </li>
                ))}
              </ul>
            </details>
          )}

        </>
      )}

      {drops.length > 0 && (
        <details className="q__whybox drops-fold">
          <summary className="muted small">留痕 {drops.length} 条</summary>
          <div className="packet__drops">
            <p className="packet__counts packet__counts--drops">
              {/* 跳过包没有"它说的 N 条"（opinions 为空），`dropHeadline` 的
                  "它说的 0 条里…"是谎言——headline 由卡片按状态自己写，
                  不碰共享函数。 */}
              {skipped ? "整包已跳过，原因如下" : dropHeadline(packet, drops)}
            </p>
            <ul className="drops">
              {drops.map((item) => (
                <li className="drop" key={`${item.handle}-${item.reason}`}>
                  <span className="mono small">{item.handle}</span>
                  <span className="drop__reason">{item.reason}</span>
                </li>
              ))}
            </ul>
          </div>
        </details>
      )}
    </article>
  );
}
