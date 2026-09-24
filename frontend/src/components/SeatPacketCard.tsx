import type { SeatPacketView } from "../api/types";
import { opinionFormText } from "../labels";
import { EvidenceChip } from "./EvidenceChip";

/** 一个席位交回的信息包：几条判断、几条疑问、依据哪些编号。**没有思维链。** */
export function SeatPacketCard({
  packet,
  order,
  running,
}: {
  packet: SeatPacketView;
  order: number;
  running: boolean;
}) {
  const pending = packet.status !== "completed";
  const stateText = pending ? (running ? "正在交回…" : "待启动") : "已完成";

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
      </div>

      {!pending && (
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
            <p className="packet__counts">
              {packet.judgment_count > 0 && <span>判断 {packet.judgment_count} 条</span>}
              {packet.question_count > 0 && <span>疑问 {packet.question_count} 个</span>}
              <span className="muted">第 {packet.pass_index} 轮</span>
            </p>
          )}

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
        </>
      )}
    </article>
  );
}
